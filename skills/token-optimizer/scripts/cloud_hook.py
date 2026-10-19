#!/usr/bin/env python3
"""Token Optimizer: repo-local hook dispatcher for Claude Code cloud sessions.

``measure.py cloud-init`` copies this file to ``.claude/to-cloud/to_cloud_hook.py``
inside a target repository, next to a small ``engine/`` directory (the Bash
output compression modules). The repo's ``.claude/settings.json`` then points
its hooks here with ``$CLAUDE_PROJECT_DIR``-relative paths. A cloud session
clones the repository, so these hooks run there even though no plugin is
installed.

Standard library only. Runs on whatever ``python3`` the cloud VM has.

Invariants
----------
* Always exits 0 and never raises. A hook failure must not block a tool call.
* Does nothing outside a Claude Code cloud session (``CLAUDE_CODE_REMOTE`` is
  not truthy), so a committed settings file does not double-run next to a local
  plugin install.
* Never commits, pushes, or touches git history. The only git calls are
  read-only (``rev-parse``).
* Works with an empty or unset ``HOME`` and with no ``~/.claude``. All state
  lives in ``$TOKEN_OPTIMIZER_SNAPSHOT_DIR`` or a temp directory, and in the
  two opt-in repo files below.
* Opt-in outputs (both off by default, see ``config.json`` next to this file):
    - ``state_out``: ``.to-cloud/<session-id>.json``, a metadata-only summary.
    - ``continuity``: ``.to-cloud/checkpoints/latest.json``, a small structured
      checkpoint restored at the next SessionStart.
  Neither file ever holds prompts, code, file contents, or tool output.

Events: SessionStart, PostToolUse, PreCompact, Stop. ``doctor`` is an
interactive command (not a hook event): ``python3 to_cloud_hook.py doctor [--json]``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_DIR = os.path.join(HERE, "engine")
CONFIG_PATH = os.path.join(HERE, "config.json")

SUMMARY_SCHEMA = "token-optimizer-cloud-summary/1"
CHECKPOINT_SCHEMA = "token-optimizer-cloud-checkpoint/1"
PLATFORM = "claude-code-cloud"
PROVENANCE = (
    "In-VM self-report by Token Optimizer hooks. Token figures are Token "
    "Optimizer estimates (about 4 characters per token), not provider-reported usage."
)

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}
_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]")
_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,96}")
_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_URL_RE = re.compile(r"https://claude\.ai/code/[A-Za-z0-9_-]{1,96}")
_BRANCH_RE = re.compile(r"[A-Za-z0-9._/@+-]{1,120}")
# Characters allowed in a restored file path: word characters (any script), dot, slash,
# dash, plus, at, tilde, and a plain space. No quotes, backticks, control characters.
_PATH_RE = re.compile(r"[\w.@+~/ -]{1,200}")
_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
MAX_FILES = 40
MAX_PATH_LEN = 200
MAX_CHECKPOINT_BYTES = 16 * 1024
ENGINE_TIMEOUT_S = 8


# --------------------------------------------------------------------------- env


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def is_cloud() -> bool:
    """True inside a Claude Code cloud VM (or when forced for tests)."""
    return _truthy("CLAUDE_CODE_REMOTE") or _truthy("TOKEN_OPTIMIZER_CLOUD_FORCE")


def project_dir() -> str:
    return os.environ.get("CLAUDE_PROJECT_DIR", "").strip() or os.getcwd()


def data_dir() -> str:
    """Ephemeral per-VM state directory (never under HOME)."""
    override = os.environ.get("TOKEN_OPTIMIZER_SNAPSHOT_DIR", "").strip()
    if override:
        return os.path.expanduser(override)
    # Fixed under /tmp (not $TMPDIR) so a Bash command the model runs later, which may see a
    # different TMPDIR than the hook did, finds the same archive. Keep equal to cloud_expand.py.
    base = "/tmp" if os.name == "posix" else tempfile.gettempdir()
    uid = os.getuid() if hasattr(os, "getuid") else 0
    return os.path.join(base, "token-optimizer-cloud-%d" % uid)


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def _flag(cfg: dict, key: str, env: str) -> bool:
    raw = os.environ.get(env, "").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSY and raw != "":
        return False
    return cfg.get(key) is True


def state_out_enabled(cfg: dict | None = None) -> bool:
    return _flag(cfg if cfg is not None else load_config(), "state_out",
                 "TOKEN_OPTIMIZER_CLOUD_STATE_OUT")


def continuity_enabled(cfg: dict | None = None) -> bool:
    return _flag(cfg if cfg is not None else load_config(), "continuity",
                 "TOKEN_OPTIMIZER_CLOUD_CONTINUITY")


def session_id(payload: dict | None = None) -> str:
    """The cloud session id (env first, then the hook payload), filename-safe."""
    raw = os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID", "").strip()
    if not raw and payload:
        sid = payload.get("session_id")
        raw = sid.strip() if isinstance(sid, str) else ""
    if not raw:
        return ""
    clean = _SAFE_ID.sub("_", raw)[:96]
    return clean or hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:24]


def transcript_url() -> str:
    """claude.ai transcript URL, built the way the cloud docs show it."""
    raw = os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID", "").strip()
    if not raw or _SAFE_ID.search(raw):
        return ""
    if raw.startswith("cse_"):
        raw = "session_" + raw[len("cse_"):]
    return "https://claude.ai/code/" + raw


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------- file util


def atomic_write(path: str, text: str, mode: int = 0o600) -> bool:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".to-cloud-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.chmod(tmp, mode)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except Exception:
        return False


# ----------------------------------------------------------------- event journal
# One byte per event, appended with O_APPEND so parallel hooks cannot lose or
# corrupt each other's counts. The journal holds no content at all.

_EVENT_CODES = {"start": b"S", "bash": b"B", "tool": b"T", "compact": b"C", "stop": b"P"}


def _journal_path(sid: str) -> str:
    return os.path.join(data_dir(), "events-%s.log" % (sid or "unknown"))


def journal_add(sid: str, event: str) -> None:
    try:
        os.makedirs(data_dir(), mode=0o700, exist_ok=True)
        fd = os.open(_journal_path(sid), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, _EVENT_CODES[event])
        finally:
            os.close(fd)
    except Exception:
        pass


def journal_counts(sid: str) -> dict:
    counts = {k: 0 for k in _EVENT_CODES}
    try:
        with open(_journal_path(sid), "rb") as fh:
            blob = fh.read(1_000_000)
        for name, code in _EVENT_CODES.items():
            counts[name] = blob.count(code)
    except OSError:
        pass
    return counts


def _meta_path(sid: str) -> str:
    return os.path.join(data_dir(), "meta-%s.json" % (sid or "unknown"))


def meta_load(sid: str) -> dict:
    try:
        with open(_meta_path(sid), "r", encoding="utf-8") as fh:
            m = json.load(fh)
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


def meta_save(sid: str, meta: dict) -> None:
    atomic_write(_meta_path(sid), json.dumps(meta, sort_keys=True))


# -------------------------------------------------------------- files touched set
# Repo-relative paths only (never contents). Used for the opt-in checkpoint.


def _touched_path(sid: str) -> str:
    return os.path.join(data_dir(), "touched-%s.txt" % (sid or "unknown"))


def _relativize(path: str) -> str:
    """Repo-relative path, or '' if it is outside the project or unsafe."""
    if not isinstance(path, str) or not path or "\n" in path or "\r" in path or "\x00" in path:
        return ""
    root = os.path.realpath(project_dir())
    full = path if os.path.isabs(path) else os.path.join(root, path)
    full = os.path.realpath(full)
    if full != root and not full.startswith(root + os.sep):
        return ""
    rel = os.path.relpath(full, root).replace(os.sep, "/")
    if rel in (".", "") or rel.startswith("../") or len(rel) > MAX_PATH_LEN:
        return ""
    if not _PATH_RE.fullmatch(rel):
        return ""
    return rel


def touched_add(sid: str, payload: dict) -> None:
    try:
        tin = payload.get("tool_input")
        if not isinstance(tin, dict):
            return
        raw = tin.get("file_path") or tin.get("notebook_path") or ""
        rel = _relativize(raw)
        if not rel:
            return
        os.makedirs(data_dir(), mode=0o700, exist_ok=True)
        fd = os.open(_touched_path(sid), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (rel + "\n").encode("utf-8", "replace"))
        finally:
            os.close(fd)
    except Exception:
        pass


def touched_list(sid: str) -> list:
    seen: dict = {}
    try:
        with open(_touched_path(sid), "r", encoding="utf-8", errors="replace") as fh:
            for line in fh.read(2_000_000).splitlines():
                line = line.strip()
                if line:
                    seen[line] = None
    except OSError:
        return []
    out = list(seen.keys())
    return out[-MAX_FILES:]


# ------------------------------------------------------------- compression stats


def compression_stats() -> dict:
    """Aggregate counters from the engine's compression_events table.

    Reads feature names and numbers only. ``command_pattern`` and ``detail`` can
    hold command text, so they are never selected.
    """
    stats = {
        "events": 0,
        "original_tokens_est": 0,
        "compressed_tokens_est": 0,
        "tokens_saved_est": 0,
        "by_feature": {},
    }
    db = os.path.join(data_dir(), "trends.db")
    if not os.path.isfile(db):
        return stats
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % quote(db), uri=True, timeout=0.5)
        try:
            rows = conn.execute(
                "SELECT feature, COUNT(*), COALESCE(SUM(original_tokens),0), "
                "COALESCE(SUM(compressed_tokens),0) FROM compression_events GROUP BY feature"
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return stats
    for feature, n, orig, comp in rows:
        name = _SAFE_ID.sub("_", str(feature or "unknown"))[:48]
        saved = max(int(orig) - int(comp), 0)
        stats["events"] += int(n)
        stats["original_tokens_est"] += int(orig)
        stats["compressed_tokens_est"] += int(comp)
        stats["tokens_saved_est"] += saved
        stats["by_feature"][name] = {"events": int(n), "tokens_saved_est": saved}
    return stats


# --------------------------------------------------------------------- git (r/o)


def _git(args: list) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", project_dir()] + args,
            capture_output=True, text=True, timeout=3,
            env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"),
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def git_state() -> dict:
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    sha = _git(["rev-parse", "HEAD"])
    if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
        sha = ""
    if not re.fullmatch(r"[A-Za-z0-9._/@+-]{1,120}", branch or ""):
        branch = ""
    return {"branch": branch, "head": sha}


# ------------------------------------------------------------------------ outputs


def engine_version() -> str:
    try:
        with open(os.path.join(HERE, "VERSION"), "r", encoding="utf-8") as fh:
            v = fh.read().strip()
        return v[:32] if re.fullmatch(r"[0-9A-Za-z.+-]+", v) else ""
    except OSError:
        return ""


def build_summary(sid: str, payload: dict | None = None) -> dict:
    counts = journal_counts(sid)
    meta = meta_load(sid)
    comp = compression_stats()
    return {
        "schema": SUMMARY_SCHEMA,
        "platform": PLATFORM,
        "provenance": PROVENANCE,
        "session_id": sid,
        "transcript_url": transcript_url(),
        "token_optimizer_version": engine_version(),
        "started_at": meta.get("started_at") or now_iso(),
        "updated_at": now_iso(),
        "counts": {
            "bash_calls": counts["bash"],
            "edit_calls": counts["tool"],
            "compactions": counts["compact"],
            "turns_ended": counts["stop"],
        },
        "compression": comp,
        "not_measured": ["credential_redactions", "provider_reported_usage"],
    }


def _repo_out_dir(*parts: str) -> str:
    """Directory under the project for an opt-in output, or '' if it is unsafe.

    The repo is untrusted input: refuse a symlinked .to-cloud (or checkpoints) and
    anything that resolves outside the project root.
    """
    root = os.path.realpath(project_dir())
    path = os.path.join(root, *parts)
    probe = root
    for part in parts:
        probe = os.path.join(probe, part)
        if os.path.islink(probe):
            return ""
    real = os.path.realpath(path)
    if real != path and not real.startswith(root + os.sep):
        return ""
    return path


def write_summary(sid: str, payload: dict | None = None) -> str:
    if not sid:
        return ""
    out_dir = _repo_out_dir(".to-cloud")
    if not out_dir:
        return ""
    path = os.path.join(out_dir, sid + ".json")
    if os.path.islink(path):
        return ""
    ok = atomic_write(path, json.dumps(build_summary(sid, payload), indent=2, sort_keys=True) + "\n")
    return path if ok else ""


def build_checkpoint(sid: str) -> dict:
    counts = journal_counts(sid)
    meta = meta_load(sid)
    git = git_state()
    return {
        "schema": CHECKPOINT_SCHEMA,
        "platform": PLATFORM,
        "session_id": sid,
        "transcript_url": transcript_url(),
        "started_at": meta.get("started_at") or now_iso(),
        "saved_at": now_iso(),
        "git": git,
        "files_touched": touched_list(sid),
        "counts": {
            "bash_calls": counts["bash"],
            "edit_calls": counts["tool"],
            "compactions": counts["compact"],
        },
    }


def write_checkpoint(sid: str) -> str:
    if not sid:
        return ""
    cp = build_checkpoint(sid)
    text = json.dumps(cp, indent=2, sort_keys=True) + "\n"
    if len(text.encode("utf-8")) > MAX_CHECKPOINT_BYTES:
        cp["files_touched"] = cp["files_touched"][-10:]
        text = json.dumps(cp, indent=2, sort_keys=True) + "\n"
    base = _repo_out_dir(".to-cloud", "checkpoints")
    if not base or os.path.islink(os.path.join(base, "latest.json")):
        return ""
    ok = atomic_write(os.path.join(base, "latest.json"), text)
    return os.path.join(base, "latest.json") if ok else ""


def load_checkpoint(path: str) -> dict | None:
    """Read and strictly validate a checkpoint file. None if anything is off.

    The file comes from the repository, so it is treated as untrusted: every field
    is matched against a narrow pattern, so no free text can reach the model.
    """
    try:
        if os.path.islink(path) or os.path.getsize(path) > MAX_CHECKPOINT_BYTES * 2:
            return None
        with open(path, "r", encoding="utf-8") as fh:
            cp = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(cp, dict) or cp.get("schema") != CHECKPOINT_SCHEMA:
        return None
    out: dict = {"schema": CHECKPOINT_SCHEMA}
    for key, rx in (("session_id", _ID_RE), ("saved_at", _TS_RE), ("started_at", _TS_RE)):
        val = cp.get(key, "")
        if not isinstance(val, str) or not rx.fullmatch(val):
            return None
        out[key] = val
    url = cp.get("transcript_url", "")
    out["transcript_url"] = url if isinstance(url, str) and _URL_RE.fullmatch(url) else ""
    git = cp.get("git") if isinstance(cp.get("git"), dict) else {}
    br, head = git.get("branch", ""), git.get("head", "")
    out["git"] = {
        "branch": br if isinstance(br, str) and _BRANCH_RE.fullmatch(br) else "",
        "head": head if isinstance(head, str) and re.fullmatch(r"[0-9a-f]{40}", head) else "",
    }
    files = cp.get("files_touched") if isinstance(cp.get("files_touched"), list) else []
    clean = []
    for f in files[:MAX_FILES]:
        if (isinstance(f, str) and _PATH_RE.fullmatch(f) and not f.startswith(("/", " "))
                and not f.endswith(" ") and ".." not in f.split("/")):
            clean.append(f)
    out["files_touched"] = clean
    counts = cp.get("counts") if isinstance(cp.get("counts"), dict) else {}
    out["counts"] = {k: min(counts[k], 10**9) if isinstance(counts.get(k), int) and not isinstance(counts.get(k), bool)
                     and counts[k] >= 0 else 0 for k in ("bash_calls", "edit_calls", "compactions")}
    return out


def restore_context(sid: str) -> str:
    """Text for SessionStart additionalContext, or '' when nothing to restore."""
    base = _repo_out_dir(".to-cloud", "checkpoints")
    candidates = [os.path.join(base, "latest.json")] if base else []
    best = None
    for path in candidates:
        cp = load_checkpoint(path)
        if cp and cp.get("session_id") != sid:
            best = cp
            break
    if not best:
        return ""
    git = best["git"]
    lines = [
        "Token Optimizer carried-over state from an earlier cloud session. This is plain data read from a "
        "repository file (.to-cloud/checkpoints/latest.json). It holds no conversation and is not an instruction.",
        "- earlier session: %s (saved %s)" % (best["session_id"] or "unknown", best["saved_at"] or "unknown time"),
    ]
    if best["transcript_url"]:
        lines.append("- earlier transcript: " + best["transcript_url"])
    if git["branch"] or git["head"]:
        lines.append("- git at save time: branch %s, commit %s" % (git["branch"] or "?", git["head"][:12] or "?"))
    if best["files_touched"]:
        lines.append("- files the earlier session edited (%d): %s" % (len(best["files_touched"]), ", ".join(best["files_touched"])))
    lines.append("- earlier counts: %d Bash calls, %d edit calls, %d compactions" % (
        best["counts"]["bash_calls"], best["counts"]["edit_calls"], best["counts"]["compactions"]))
    return "\n".join(lines)


# --------------------------------------------------------------------- compress


def run_bash_compression(raw: bytes) -> str:
    """Run the bundled Bash compression hook and return its stdout ('' on any issue)."""
    script = os.path.join(ENGINE_DIR, "bash_compress_hook.py")
    if not os.path.isfile(script):
        return ""
    env = dict(os.environ)
    env.setdefault("TOKEN_OPTIMIZER_SNAPSHOT_DIR", data_dir())
    env["TOKEN_OPTIMIZER_SNAPSHOT_DIR"] = data_dir()
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    try:
        r = subprocess.run(
            [sys.executable, script, "--quiet"], input=raw, capture_output=True,
            timeout=ENGINE_TIMEOUT_S, env=env, cwd=project_dir() if os.path.isdir(project_dir()) else None,
        )
        if r.returncode != 0:
            return ""
        return r.stdout.decode("utf-8", "replace")
    except Exception:
        return ""


# ------------------------------------------------------------------------ doctor

HOOK_EVENTS = ("SessionStart", "PostToolUse", "PreCompact", "Stop")
MARKER = "to-cloud/to_cloud_hook.py"


def is_cloud_env() -> bool:
    return _truthy("CLAUDE_CODE_REMOTE")


def _is_ours(group: object) -> bool:
    hooks = group.get("hooks") if isinstance(group, dict) else None
    return isinstance(hooks, list) and any(
        isinstance(h, dict) and MARKER in str(h.get("command", "")) for h in hooks)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def doctor_report(target: str | None = None) -> dict:
    """Report which Token Optimizer route is live in this cloud session."""
    target = os.path.realpath(target or project_dir())
    cfg_dir = os.path.join(target, ".claude", "to-cloud")
    checks: list = []

    def add(status: str, name: str, detail: str) -> None:
        checks.append({"status": status, "name": name, "detail": detail})

    cloud = is_cloud_env()
    raw_sid = os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID", "").strip()
    if cloud:
        add("OK", "Environment", "Claude Code cloud session (CLAUDE_CODE_REMOTE=true)")
        add("OK" if raw_sid else "--", "Session id", raw_sid or "CLAUDE_CODE_REMOTE_SESSION_ID not set")
        if transcript_url():
            add("OK", "Transcript URL", transcript_url())
    else:
        add("--", "Environment", "not a Claude Code cloud session (CLAUDE_CODE_REMOTE is not 'true'); "
                                 "checking the repo kit only")

    settings_ok = False
    try:
        spath = os.path.join(target, ".claude", "settings.json")
        with open(spath, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        hooks = data.get("hooks") if isinstance(data, dict) and isinstance(data.get("hooks"), dict) else {}
        wired = [e for e in HOOK_EVENTS
                 if isinstance(hooks.get(e), list) and any(_is_ours(g) for g in hooks[e])]
        settings_ok = len(wired) == len(HOOK_EVENTS)
        add("OK" if settings_ok else "!!", "Repo hooks block",
            "all %d events wired in .claude/settings.json" % len(wired) if settings_ok
            else "missing events: %s (run cloud-init)" % ", ".join(sorted(set(HOOK_EVENTS) - set(wired))))
    except (OSError, ValueError) as exc:
        add("!!", "Repo hooks block", "cannot read .claude/settings.json (%s)" % type(exc).__name__)

    kit_ok = False
    try:
        with open(os.path.join(cfg_dir, "manifest.json"), "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        files = manifest.get("files", {})
        bad = []
        for rel, digest in files.items():
            p = os.path.join(target, rel)
            if not os.path.isfile(p) or _sha256_file(p) != digest:
                bad.append(rel)
        kit_ok = not bad
        add("OK" if kit_ok else "!!", "Repo kit files",
            "%d files match the manifest (kit version %s)" % (len(files), manifest.get("token_optimizer_version") or "unknown")
            if kit_ok else "changed or missing: %s" % ", ".join(sorted(bad)[:6]))
    except (OSError, ValueError, AttributeError):
        add("!!", "Repo kit files", "no manifest at .claude/to-cloud/manifest.json (run cloud-init)")

    sid = session_id({})
    fired = sum(journal_counts(sid).values()) if cloud else 0
    if cloud:
        add("OK" if fired else "??", "Repo hooks fired this session",
            "%d events recorded in the VM journal" % fired if fired
            else "no events yet (SessionStart fires once at start; check again after a Bash call)")

    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "").strip()
    if plugin_root:
        add("OK", "Plugin route", "CLAUDE_PLUGIN_ROOT is set (%s): a plugin is loaded in this environment" % plugin_root)
    else:
        add("--", "Plugin route", "no CLAUDE_PLUGIN_ROOT in this environment: no plugin loaded here")

    if plugin_root and fired:
        live = "both plugin and repo hooks: expect duplicate hook work"
    elif plugin_root:
        live = "plugin"
    elif fired:
        live = "repo-local hooks (cloud-init)"
    elif settings_ok and kit_ok:
        live = "repo-local hooks installed, not yet seen firing"
    else:
        live = "none"
    add("!!" if live == "none" else "OK", "Live route", live)

    cfg: dict = {}
    try:
        with open(os.path.join(cfg_dir, "config.json"), "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        cfg = loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        pass
    add("--", "State-out summary", "on" if state_out_enabled(cfg) else "off (opt-in)")
    add("--", "Continuity checkpoint", "on" if continuity_enabled(cfg) else "off (opt-in)")

    try:
        d = data_dir()
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".probe")
        with open(probe, "w") as fh:
            fh.write("x")
        os.unlink(probe)
        add("OK", "VM state directory", "%s writable (HOME is not used)" % d)
    except OSError as exc:
        add("!!", "VM state directory", "not writable (%s)" % type(exc).__name__)

    caveats = [
        "State is per session: the VM and its state directory are gone when the session ends.",
        "Single-repository sessions only. With several repositories Claude Code reads only enabledPlugins and "
        "extraKnownMarketplaces from each repo's settings, so these hooks do not run.",
        "Numbers are an in-VM self-report with Token Optimizer token estimates. They are not provider-reported usage "
        "and are kept separate from it.",
        "CLAUDE_CODE_REMOTE does not tell Claude Code cloud and Cowork apart. This kit supports Claude Code cloud only.",
    ]
    return {"cloud": cloud, "target": target, "live_route": live, "checks": checks, "caveats": caveats}


def main_doctor(argv: list) -> int:
    as_json = "--json" in argv
    rest = [a for a in argv if a != "--json"]
    target = None
    if "--target" in rest:
        i = rest.index("--target")
        if i + 1 < len(rest):
            target = rest[i + 1]
    report = doctor_report(target)
    if as_json:
        print(json.dumps(report, indent=2))
        return 0
    print("Token Optimizer: cloud doctor")
    print("  target: %s" % report["target"])
    for c in report["checks"]:
        print("  [%s] %s: %s" % (c["status"], c["name"], c["detail"]))
    print("  Caveats:")
    for c in report["caveats"]:
        print("   - " + c)
    return 0



# ----------------------------------------------------------------------- events


def _read_payload() -> tuple:
    try:
        raw = sys.stdin.buffer.read(6_000_000)
    except Exception:
        return b"", {}
    try:
        data = json.loads(raw.decode("utf-8", "replace")) if raw.strip() else {}
        return raw, data if isinstance(data, dict) else {}
    except ValueError:
        return raw, {}


def handle(event: str) -> None:
    if event == "doctor":
        return
    if not is_cloud():
        return
    raw, payload = _read_payload()
    sid = session_id(payload)
    cfg = load_config()

    if event == "SessionStart":
        meta = meta_load(sid)
        if not meta.get("started_at"):
            meta_save(sid, {"started_at": now_iso()})
        journal_add(sid, "start")
        if continuity_enabled(cfg):
            ctx = restore_context(sid)
            if ctx:
                sys.stdout.write(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "SessionStart", "additionalContext": ctx}}))
        return

    if event == "PostToolUse":
        tool = payload.get("tool_name")
        if tool == "Bash":
            journal_add(sid, "bash")
            out = run_bash_compression(raw)
            if out.strip():
                sys.stdout.write(out)
        elif tool in _EDIT_TOOLS:
            journal_add(sid, "tool")
            if continuity_enabled(cfg):
                touched_add(sid, payload)
        return

    if event == "PreCompact":
        journal_add(sid, "compact")
        if continuity_enabled(cfg):
            write_checkpoint(sid)
        return

    if event == "Stop":
        journal_add(sid, "stop")
        if state_out_enabled(cfg):
            write_summary(sid, payload)
        if continuity_enabled(cfg):
            write_checkpoint(sid)
        return


def main(argv: list | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "doctor":  # interactive command, not a hook event
        try:
            return main_doctor(argv[1:])
        except Exception:  # noqa: BLE001
            return 0
    try:
        if argv:
            handle(argv[0])
    except BaseException:  # noqa: BLE001 - a hook must never fail the tool call
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
