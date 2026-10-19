#!/usr/bin/env python3
"""Token Optimizer: Claude Code cloud sessions (``cloud-init`` / ``cloud-doctor``).

A Claude Code cloud session (claude.ai/code) starts from a fresh clone of the
repository on a fresh VM. Plugins and marketplaces declared in the repo's
``.claude/settings.json`` are not installed there (Anthropic's cloud-environments
doc, "What carries over from your setup"), so the plugin install does not give a
cloud session Token Optimizer. What does carry over is whatever is committed to
the repo: ``.claude/settings.json`` hooks, ``.claude/skills/`` and scripts.

``cloud-init`` writes exactly that into a target repository:

    .claude/settings.json                         hooks block (merged, idempotent)
    .claude/to-cloud/to_cloud_hook.py             hook dispatcher (stdlib only)
    .claude/to-cloud/engine/*.py                  Bash output compression modules, plus an
                                                  expand-only measure.py (cloud_expand.py)
    .claude/to-cloud/config.json                  opt-in switches (both off)
    .claude/to-cloud/manifest.json                sha256 of every file written
    .claude/skills/token-optimizer-cloud/SKILL.md short skill describing the above

Scope: Claude Code cloud, one repository per session. Cowork cloud is not
covered. In a session with several repositories Claude Code reads only the
``enabledPlugins`` and ``extraKnownMarketplaces`` keys from each repo's settings,
so these hooks do not run there.

Nothing here commits or pushes. The two opt-in outputs are written to the
working tree and left for the user to review.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent

# Modules the Bash compression hook needs. Kept explicit and small: the cloud
# build is not the full plugin. tests/test_cloud_init.py proves this set is
# closed (the bundled hook runs from a directory holding only these files).
ENGINE_MODULES = (
    "archive_result", "bash_compress", "bash_compress_hook", "bash_whitelist",
    "build_output_compress", "command_filters", "compression_log",
    "credential_patterns", "delta_diff", "hook_io", "hook_runtime",
    "pipeline_analyzer", "plugin_env", "recovery_output", "refetch_fingerprint",
    "runtime_env", "session_store", "thrash_guard", "token_estimate", "utf8_io",
)

MARKER = "to-cloud/to_cloud_hook.py"  # keep equal to cloud_hook.MARKER (tested)
TO_DIR = Path(".claude") / "to-cloud"
SKILL_DIR = Path(".claude") / "skills" / "token-optimizer-cloud"
SETTINGS = Path(".claude") / "settings.json"

_POST_MATCHER = "Bash|Edit|Write|MultiEdit|NotebookEdit"

# event -> (matcher or None, timeout seconds)
HOOK_EVENTS = {
    "SessionStart": (None, 10),
    "PostToolUse": (_POST_MATCHER, 15),
    "PreCompact": (None, 10),
    "Stop": (None, 10),
}

SKILL_MD = """---
name: token-optimizer-cloud
description: Explains what Token Optimizer does in this repository's Claude Code cloud sessions, how to read its per-session numbers, and how to opt in to the metadata summary and the checkpoint that carries state to the next session.
---

# Token Optimizer in cloud sessions

This repository was set up with `cloud-init`. In a Claude Code cloud session
(`CLAUDE_CODE_REMOTE=true`) the hooks in `.claude/settings.json` run
`.claude/to-cloud/to_cloud_hook.py`. Outside cloud sessions they do nothing.

## What it does

- Compresses large Bash tool output after the command has run, and keeps the full
  original recoverable in the VM for the rest of the session.
- Counts Bash calls, edits and compactions for the session.
- Works for single-repository sessions only.

## What it does not do

- It is not provider-reported usage. Token figures are Token Optimizer estimates
  from inside the VM. Do not add them to, or compare them with, numbers from
  Anthropic.
- State lives in the VM and is gone when the session ends, unless a file below
  is written and the user commits it.
- It never commits, pushes, or reads prompts, code or file contents into its
  output files.

## Opt-in outputs (both off by default)

Set in `.claude/to-cloud/config.json` or with the environment variables
`TOKEN_OPTIMIZER_CLOUD_STATE_OUT=1` and `TOKEN_OPTIMIZER_CLOUD_CONTINUITY=1`.

- `state_out` writes `.to-cloud/<session-id>.json` at the end of each turn:
  counters, timestamps, session id and transcript URL. Nothing else.
- `continuity` writes `.to-cloud/checkpoints/latest.json`: git branch and commit,
  the repo-relative paths of files edited this session, and counters. The next
  session restores it at SessionStart as plain data, not instructions.

## Rules for you

- Do not run `git add`, `git commit` or `git push` on anything under `.to-cloud/`
  unless the user asks. If the user wants state carried to the next session,
  offer to commit `.to-cloud/checkpoints/latest.json` and wait for a yes.
- Do not paste the contents of these files into prompts, PRs or comments.
- To check health, run `python3 "$CLAUDE_PROJECT_DIR/.claude/to-cloud/to_cloud_hook.py" doctor`. It reports whether the repo hooks are wired and have fired, and which route is live.
"""


# ----------------------------------------------------------------------- helpers


def _hook():
    """The cloud_hook module (single source for events, marker and the doctor)."""
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        import cloud_hook  # noqa: PLC0415
    finally:
        try:
            sys.path.remove(str(SCRIPTS_DIR))
        except ValueError:
            pass
    return cloud_hook


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_write(path: Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".to-cloud-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def plugin_version() -> str:
    """Version of the checkout this script runs from ('' when unknown)."""
    for rel in ("../../../.claude-plugin/plugin.json", "../../../package.json"):
        try:
            data = json.loads((SCRIPTS_DIR / rel).read_text(encoding="utf-8"))
            v = data.get("version")
            if isinstance(v, str) and v:
                return v
        except (OSError, ValueError):
            continue
    return ""


def hook_command(event: str) -> str:
    """Shell command for one hook event.

    ``$CLAUDE_PROJECT_DIR`` is the repo root in a cloud session. The command
    exits 0 (no-op) when the script or python3 is missing, so a repo that carries
    this settings file never breaks a session on a machine without them.
    """
    return (
        'P="${CLAUDE_PROJECT_DIR:-.}/.claude/' + "to-cloud/to_cloud_hook.py" + '"; '
        '[ -r "$P" ] && command -v python3 >/dev/null 2>&1 || exit 0; '
        'exec python3 "$P" ' + event
    )


def _our_entry(event: str) -> dict:
    matcher, timeout = HOOK_EVENTS[event]
    entry: dict = {}
    if matcher:
        entry["matcher"] = matcher
    entry["hooks"] = [{"type": "command", "command": hook_command(event), "timeout": timeout}]
    return entry


def _is_ours(group: object) -> bool:
    if not isinstance(group, dict):
        return False
    hooks = group.get("hooks")
    if not isinstance(hooks, list):
        return False
    return any(isinstance(h, dict) and MARKER in str(h.get("command", "")) for h in hooks)


def merge_settings(settings: dict) -> dict:
    """Return settings with our hooks added; everything else is left as it was."""
    out = dict(settings)
    hooks = out.get("hooks")
    if hooks is None:
        hooks = {}
    if not isinstance(hooks, dict):
        raise ValueError("'hooks' in .claude/settings.json is not an object")
    hooks = dict(hooks)
    for event in HOOK_EVENTS:
        groups = hooks.get(event, [])
        if not isinstance(groups, list):
            raise ValueError("hooks.%s in .claude/settings.json is not a list" % event)
        kept = [g for g in groups if not _is_ours(g)]
        hooks[event] = kept + [_our_entry(event)]
    out["hooks"] = hooks
    return out


def strip_settings(settings: dict) -> dict:
    out = dict(settings)
    hooks = out.get("hooks")
    if not isinstance(hooks, dict):
        return out
    hooks = dict(hooks)
    for event in list(hooks):
        groups = hooks[event]
        if isinstance(groups, list):
            kept = [g for g in groups if not _is_ours(g)]
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
    if hooks:
        out["hooks"] = hooks
    else:
        out.pop("hooks", None)
    return out


def _load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(".claude/settings.json is not a JSON object")
    return data


def _dump_settings(data: dict) -> bytes:
    return (json.dumps(data, indent=2) + "\n").encode("utf-8")


def _read_config(target: Path) -> dict:
    try:
        cfg = json.loads((target / TO_DIR / "config.json").read_text(encoding="utf-8"))
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


# ----------------------------------------------------------------------- install


def install(target: Path, state_out=None, continuity=None, gitignore=False, dry_run=False) -> dict:
    """Write the cloud kit into ``target``. Returns a report dict.

    ``state_out`` / ``continuity``: True or False sets the switch, None keeps the
    current value (default off).
    """
    target = Path(target).resolve()
    if not target.is_dir():
        raise ValueError("target is not a directory: %s" % target)
    if not (target / ".git").exists():
        raise ValueError("target is not the root of a git repository (no .git): %s" % target)
    missing = [m for m in ENGINE_MODULES if not (SCRIPTS_DIR / (m + ".py")).is_file()]
    if missing or not (SCRIPTS_DIR / "cloud_hook.py").is_file() or not (SCRIPTS_DIR / "cloud_expand.py").is_file():
        raise ValueError("incomplete Token Optimizer checkout, missing: %s" % ", ".join(missing or ["cloud_hook.py"]))

    settings_path = target / SETTINGS
    new_settings = merge_settings(_load_settings(settings_path))  # raises on bad JSON/shape

    cfg = _read_config(target)
    if state_out is not None:
        cfg["state_out"] = bool(state_out)
    if continuity is not None:
        cfg["continuity"] = bool(continuity)
    cfg.setdefault("state_out", False)
    cfg.setdefault("continuity", False)

    version = plugin_version()
    files: dict = {}
    planned: dict = {}
    planned[TO_DIR / "to_cloud_hook.py"] = (SCRIPTS_DIR / "cloud_hook.py").read_bytes()
    for m in ENGINE_MODULES:
        planned[TO_DIR / "engine" / (m + ".py")] = (SCRIPTS_DIR / (m + ".py")).read_bytes()
    # The engine tells the model to run `<engine dir>/measure.py expand <id>`. In the kit that
    # file is the small expand-only helper, so the printed command works in a cloud session.
    planned[TO_DIR / "engine" / "measure.py"] = (SCRIPTS_DIR / "cloud_expand.py").read_bytes()
    planned[TO_DIR / "VERSION"] = (version + "\n").encode("utf-8")
    planned[SKILL_DIR / "SKILL.md"] = SKILL_MD.encode("utf-8")
    planned[TO_DIR / "config.json"] = (json.dumps(cfg, indent=2, sort_keys=True) + "\n").encode("utf-8")
    for rel, data in planned.items():
        files[rel.as_posix()] = hashlib.sha256(data).hexdigest()
    manifest = {
        "schema": "token-optimizer-cloud-manifest/1",
        "token_optimizer_version": version,
        "files": {k: v for k, v in files.items() if not k.endswith("config.json")},
    }
    planned[TO_DIR / "manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")

    report = {
        "target": str(target),
        "dry_run": bool(dry_run),
        "files": sorted(p.as_posix() for p in planned) + [SETTINGS.as_posix()],
        "state_out": cfg["state_out"],
        "continuity": cfg["continuity"],
        "gitignore": False,
        "version": version,
    }
    if dry_run:
        return report

    # Stale engine files from an older kit are removed so the bundle is exactly
    # the manifest (a leftover module could shadow a newer one).
    engine_dir = target / TO_DIR / "engine"
    if engine_dir.is_dir():
        for old in engine_dir.glob("*.py"):
            if old.stem not in ENGINE_MODULES and old.stem != "measure":
                old.unlink()
    for rel, data in planned.items():
        _atomic_write(target / rel, data, 0o755 if rel.name == "to_cloud_hook.py" else 0o644)
    _atomic_write(settings_path, _dump_settings(new_settings))

    if gitignore:
        gi = target / ".gitignore"
        line = "/.to-cloud/*.json"
        existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
        if line not in existing.splitlines():
            sep = "" if not existing or existing.endswith("\n") else "\n"
            _atomic_write(gi, (existing + sep + "# Token Optimizer cloud summaries (metadata, local only)\n" + line + "\n").encode("utf-8"))
        report["gitignore"] = True
    return report


def uninstall(target: Path) -> dict:
    target = Path(target).resolve()
    removed = []
    settings_path = target / SETTINGS
    if settings_path.exists():
        data = _load_settings(settings_path)
        stripped = strip_settings(data)
        if stripped != data:
            if stripped:
                _atomic_write(settings_path, _dump_settings(stripped))
            else:
                settings_path.unlink()
            removed.append(SETTINGS.as_posix() + " (hooks block)")
    if (target / TO_DIR).is_dir():
        shutil.rmtree(target / TO_DIR)
        removed.append(TO_DIR.as_posix())
    skill = target / SKILL_DIR / "SKILL.md"
    if skill.is_file() and "This repository was set up with `cloud-init`" in skill.read_text(encoding="utf-8", errors="replace"):
        shutil.rmtree(target / SKILL_DIR)
        removed.append(SKILL_DIR.as_posix())
    return {"target": str(target), "removed": removed,
            "note": "Files under .to-cloud/ (summaries, checkpoints) were left in place."}


# ------------------------------------------------------------------------ doctor
# The doctor lives in cloud_hook.py so it also ships inside the repo kit
# (``to_cloud_hook.py doctor``) and can run in a cloud session with no plugin.


def detect_cloud() -> bool:
    return _hook().is_cloud_env()


def cloud_doctor(target: Path | None = None) -> dict:
    return _hook().doctor_report(str(target) if target else None)


def main_doctor(argv: list) -> int:
    return _hook().main_doctor(argv)


# --------------------------------------------------------------------------- CLI

_USAGE = """usage: measure.py cloud-init [--target DIR] [--state-out | --no-state-out]
                              [--continuity | --no-continuity] [--gitignore]
                              [--dry-run] [--uninstall] [--json]

Writes Token Optimizer's Claude Code cloud kit into a git repository so cloud
sessions (claude.ai/code) get Bash output compression from committed hooks.
Review the diff and commit it yourself: this command never commits or pushes.

  --state-out      write .to-cloud/<session-id>.json (metadata only) each turn
  --continuity     write .to-cloud/checkpoints/latest.json and restore it at
                   the next SessionStart (branch, commit, files edited, counts)
  --gitignore      add /.to-cloud/*.json to .gitignore (summaries stay local)
  --uninstall      remove the hooks block and the kit; .to-cloud/ files stay

Single-repository cloud sessions only. Cowork is not covered."""


def main_init(argv: list) -> int:
    if "-h" in argv or "--help" in argv:
        print(_USAGE)
        return 0
    flags = {"state_out": None, "continuity": None}
    target = Path.cwd()
    as_json = dry = gitignore = remove = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--target" and i + 1 < len(argv):
            target = Path(argv[i + 1])
            i += 1
        elif a == "--state-out":
            flags["state_out"] = True
        elif a == "--no-state-out":
            flags["state_out"] = False
        elif a == "--continuity":
            flags["continuity"] = True
        elif a == "--no-continuity":
            flags["continuity"] = False
        elif a == "--gitignore":
            gitignore = True
        elif a == "--dry-run":
            dry = True
        elif a == "--uninstall":
            remove = True
        elif a == "--json":
            as_json = True
        else:
            print("cloud-init: unknown argument %r\n" % a, file=sys.stderr)
            print(_USAGE, file=sys.stderr)
            return 2
        i += 1
    try:
        if remove:
            report = uninstall(target)
        else:
            report = install(target, flags["state_out"], flags["continuity"], gitignore, dry)
    except (ValueError, OSError) as exc:
        print("cloud-init: %s" % exc, file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(report, indent=2))
        return 0
    if remove:
        print("Removed: " + (", ".join(report["removed"]) or "nothing found"))
        print(report["note"])
        return 0
    print("%s Token Optimizer cloud kit for %s" % ("Would write" if dry else "Wrote", report["target"]))
    for f in report["files"]:
        print("  " + f)
    print("State-out summary: %s | Continuity checkpoint: %s (both are opt-in)" % (
        "on" if report["state_out"] else "off", "on" if report["continuity"] else "off"))
    if not dry:
        print("")
        print("Next: review the diff, then commit and push it yourself (this command does not).")
        print("Scope: Claude Code cloud, single-repository sessions. Cowork is not covered.")
        print("Numbers from the kit are an in-VM self-report, not provider-reported usage.")
        if not report.get("gitignore"):
            print("Summaries are local files; add --gitignore to keep .to-cloud/*.json out of commits.")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    rest = sys.argv[2:]
    sys.exit(main_doctor(rest) if cmd == "doctor" else main_init(rest))
