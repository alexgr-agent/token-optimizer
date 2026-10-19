#!/usr/bin/env python3
"""Token Optimizer: ``cloud-import`` - ingest Claude Code cloud session summaries.

Cloud sessions (see ``cloud_init.py``) can write a metadata-only summary
``.to-cloud/<session-id>.json`` when the user opts in. This command reads those
files from a local checkout and records them in the local ``trends.db`` so cloud
sessions show up beside local ones.

Honest labeling, enforced here:

* Summaries and checkpoints go to their own tables, ``cloud_session_summaries``
  and ``cloud_checkpoints``, and never into
  ``session_log``. ``session_log`` holds provider-reported token usage read from
  transcripts. A summary is an in-VM self-report with Token Optimizer estimates,
  so mixing them would present estimates as provider numbers.
* Every row carries ``platform = 'claude-code-cloud'`` and the provenance label
  written by the hook.
* The files are untrusted input. Each one is size-capped and validated field by
  field, and only known numeric and short-string fields are stored. Nothing
  from a file is executed or echoed back unvalidated.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "token-optimizer-cloud-summary/1"
CHECKPOINT_SCHEMA = "token-optimizer-cloud-checkpoint/1"
PLATFORM = "claude-code-cloud"
MAX_FILE_BYTES = 256 * 1024
MAX_FILES = 5000
MAX_INT = 10**12
_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
_FEATURE_RE = re.compile(r"^[A-Za-z0-9_-]{1,48}$")
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_VERSION_RE = re.compile(r"^[0-9A-Za-z.+-]{0,32}$")

_DDL = """
CREATE TABLE IF NOT EXISTS cloud_session_summaries (
    session_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    provenance TEXT NOT NULL,
    transcript_url TEXT,
    token_optimizer_version TEXT,
    started_at TEXT,
    updated_at TEXT,
    bash_calls INTEGER DEFAULT 0,
    edit_calls INTEGER DEFAULT 0,
    compactions INTEGER DEFAULT 0,
    turns_ended INTEGER DEFAULT 0,
    compression_events INTEGER DEFAULT 0,
    original_tokens_est INTEGER DEFAULT 0,
    compressed_tokens_est INTEGER DEFAULT 0,
    tokens_saved_est INTEGER DEFAULT 0,
    by_feature_json TEXT,
    source_file TEXT,
    imported_at TEXT
);

CREATE TABLE IF NOT EXISTS cloud_checkpoints (
    session_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    transcript_url TEXT,
    started_at TEXT,
    saved_at TEXT,
    git_branch TEXT,
    git_head TEXT,
    files_touched_json TEXT,
    bash_calls INTEGER DEFAULT 0,
    edit_calls INTEGER DEFAULT 0,
    compactions INTEGER DEFAULT 0,
    source_file TEXT,
    imported_at TEXT
);
"""


class SummaryError(ValueError):
    pass


def _int(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > MAX_INT:
        raise SummaryError("%s must be an integer in 0..%d" % (name, MAX_INT))
    return value


def _ts(value, name: str) -> str:
    if not isinstance(value, str) or not _TS_RE.match(value):
        raise SummaryError("%s must be a UTC timestamp like 2026-10-19T08:00:00Z" % name)
    return value


def validate(doc: object) -> dict:
    """Return the sanitized record for a summary document, or raise SummaryError."""
    if not isinstance(doc, dict):
        raise SummaryError("not a JSON object")
    if doc.get("schema") != SCHEMA:
        raise SummaryError("unknown schema %r" % (str(doc.get("schema"))[:60],))
    if doc.get("platform") != PLATFORM:
        raise SummaryError("platform is not %s" % PLATFORM)
    sid = doc.get("session_id")
    if not isinstance(sid, str) or not _SESSION_RE.match(sid):
        raise SummaryError("bad session_id")
    prov = doc.get("provenance")
    if not isinstance(prov, str) or not (10 <= len(prov) <= 400) or any(ord(c) < 32 for c in prov):
        raise SummaryError("bad provenance label")
    url = doc.get("transcript_url", "")
    if not isinstance(url, str) or (url and not re.match(r"^https://claude\.ai/code/[A-Za-z0-9_-]{1,96}$", url)):
        raise SummaryError("bad transcript_url")
    ver = doc.get("token_optimizer_version", "")
    if not isinstance(ver, str) or not _VERSION_RE.match(ver):
        raise SummaryError("bad token_optimizer_version")
    counts = doc.get("counts")
    comp = doc.get("compression")
    if not isinstance(counts, dict) or not isinstance(comp, dict):
        raise SummaryError("missing counts or compression")
    by_feature = comp.get("by_feature", {})
    if not isinstance(by_feature, dict) or len(by_feature) > 64:
        raise SummaryError("bad by_feature")
    clean_features = {}
    for name, val in by_feature.items():
        if not isinstance(name, str) or not _FEATURE_RE.match(name) or not isinstance(val, dict):
            raise SummaryError("bad feature entry")
        clean_features[name] = {"events": _int(val.get("events", 0), "events"),
                                "tokens_saved_est": _int(val.get("tokens_saved_est", 0), "tokens_saved_est")}
    return {
        "session_id": sid,
        "platform": PLATFORM,
        "provenance": prov,
        "transcript_url": url,
        "token_optimizer_version": ver,
        "started_at": _ts(doc.get("started_at"), "started_at"),
        "updated_at": _ts(doc.get("updated_at"), "updated_at"),
        "bash_calls": _int(counts.get("bash_calls", 0), "bash_calls"),
        "edit_calls": _int(counts.get("edit_calls", 0), "edit_calls"),
        "compactions": _int(counts.get("compactions", 0), "compactions"),
        "turns_ended": _int(counts.get("turns_ended", 0), "turns_ended"),
        "compression_events": _int(comp.get("events", 0), "compression.events"),
        "original_tokens_est": _int(comp.get("original_tokens_est", 0), "original_tokens_est"),
        "compressed_tokens_est": _int(comp.get("compressed_tokens_est", 0), "compressed_tokens_est"),
        "tokens_saved_est": _int(comp.get("tokens_saved_est", 0), "tokens_saved_est"),
        "by_feature": clean_features,
    }


def validate_checkpoint(doc: object) -> dict:
    """Sanitized record for a checkpoint document, or raise SummaryError."""
    if not isinstance(doc, dict) or doc.get("schema") != CHECKPOINT_SCHEMA:
        raise SummaryError("not a cloud checkpoint")
    if doc.get("platform") != PLATFORM:
        raise SummaryError("platform is not %s" % PLATFORM)
    sid = doc.get("session_id")
    if not isinstance(sid, str) or not _SESSION_RE.match(sid):
        raise SummaryError("bad session_id")
    url = doc.get("transcript_url", "")
    if not isinstance(url, str) or (url and not re.match(r"^https://claude\.ai/code/[A-Za-z0-9_-]{1,96}$", url)):
        raise SummaryError("bad transcript_url")
    git = doc.get("git") if isinstance(doc.get("git"), dict) else {}
    branch, head = git.get("branch", ""), git.get("head", "")
    if not isinstance(branch, str) or (branch and not re.fullmatch(r"[A-Za-z0-9._/@+-]{1,120}", branch)):
        raise SummaryError("bad git branch")
    if not isinstance(head, str) or (head and not re.fullmatch(r"[0-9a-f]{40}", head)):
        raise SummaryError("bad git head")
    files = doc.get("files_touched", [])
    if not isinstance(files, list) or len(files) > 100:
        raise SummaryError("bad files_touched")
    for f in files:
        if (not isinstance(f, str) or not 0 < len(f) <= 200 or f.startswith("/")
                or ".." in f.split("/") or any(ord(c) < 32 or ord(c) == 127 for c in f)):
            raise SummaryError("bad path in files_touched")
    counts = doc.get("counts") if isinstance(doc.get("counts"), dict) else {}
    return {
        "session_id": sid, "platform": PLATFORM, "transcript_url": url,
        "started_at": _ts(doc.get("started_at"), "started_at"),
        "saved_at": _ts(doc.get("saved_at"), "saved_at"),
        "git_branch": branch, "git_head": head, "files": list(files),
        "bash_calls": _int(counts.get("bash_calls", 0), "bash_calls"),
        "edit_calls": _int(counts.get("edit_calls", 0), "edit_calls"),
        "compactions": _int(counts.get("compactions", 0), "compactions"),
    }


def _db_path() -> Path:
    scripts = str(Path(__file__).resolve().parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from plugin_env import resolve_snapshot_dir  # noqa: PLC0415
    return resolve_snapshot_dir() / "trends.db"


def _collect(paths: list) -> list:
    files: list = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files.extend(sorted(x for x in p.glob("*.json") if x.is_file() and not x.is_symlink()))
            cp = p / "checkpoints"
            if cp.is_dir() and not cp.is_symlink():
                files.extend(sorted(x for x in cp.glob("*.json") if x.is_file() and not x.is_symlink()))
        elif p.is_file() and not p.is_symlink():
            files.append(p)
    return files[:MAX_FILES]


def import_files(paths: list, db_path: Path | None = None) -> dict:
    files = _collect(paths)
    db_path = Path(db_path) if db_path else _db_path()
    imported, skipped, unchanged, cp_imported = [], [], [], []
    records = []
    for f in files:
        try:
            if f.stat().st_size > MAX_FILE_BYTES:
                raise SummaryError("file larger than %d bytes" % MAX_FILE_BYTES)
            doc = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and doc.get("schema") == CHECKPOINT_SCHEMA:
                rec = validate_checkpoint(doc)
                rec["_kind"] = "checkpoint"
            else:
                rec = validate(doc)
                rec["_kind"] = "summary"
        except (OSError, ValueError) as exc:  # SummaryError and JSON errors are ValueError
            skipped.append({"file": f.name, "reason": str(exc)[:160]})
            continue
        records.append((f, rec))
    if records:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), timeout=5)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.executescript(_DDL)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            for f, rec in records:
                if rec["_kind"] == "checkpoint":
                    row = conn.execute("SELECT saved_at FROM cloud_checkpoints WHERE session_id=?",
                                       (rec["session_id"],)).fetchone()
                    if row and row[0] and row[0] >= rec["saved_at"]:
                        unchanged.append(rec["session_id"])
                        continue
                    conn.execute(
                        "INSERT OR REPLACE INTO cloud_checkpoints (session_id, platform, transcript_url, started_at, "
                        "saved_at, git_branch, git_head, files_touched_json, bash_calls, edit_calls, compactions, "
                        "source_file, imported_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (rec["session_id"], rec["platform"], rec["transcript_url"], rec["started_at"],
                         rec["saved_at"], rec["git_branch"], rec["git_head"], json.dumps(rec["files"]),
                         rec["bash_calls"], rec["edit_calls"], rec["compactions"], f.name, now))
                    cp_imported.append(rec["session_id"])
                    continue
                row = conn.execute("SELECT updated_at FROM cloud_session_summaries WHERE session_id=?",
                                   (rec["session_id"],)).fetchone()
                if row and row[0] and row[0] >= rec["updated_at"]:
                    unchanged.append(rec["session_id"])
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO cloud_session_summaries (session_id, platform, provenance, "
                    "transcript_url, token_optimizer_version, started_at, updated_at, bash_calls, edit_calls, "
                    "compactions, turns_ended, compression_events, original_tokens_est, compressed_tokens_est, "
                    "tokens_saved_est, by_feature_json, source_file, imported_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rec["session_id"], rec["platform"], rec["provenance"], rec["transcript_url"],
                     rec["token_optimizer_version"], rec["started_at"], rec["updated_at"], rec["bash_calls"],
                     rec["edit_calls"], rec["compactions"], rec["turns_ended"], rec["compression_events"],
                     rec["original_tokens_est"], rec["compressed_tokens_est"], rec["tokens_saved_est"],
                     json.dumps(rec["by_feature"], sort_keys=True), f.name, now))
                imported.append(rec)
            conn.commit()
        finally:
            conn.close()
    return {
        "db": str(db_path),
        "imported": [r["session_id"] for r in imported],
        "checkpoints_imported": cp_imported,
        "unchanged": unchanged,
        "skipped": skipped,
        "tokens_saved_est": sum(r["tokens_saved_est"] for r in imported),
        "label": "platform=%s; in-VM self-report with Token Optimizer estimates; not provider-reported usage" % PLATFORM,
    }


def main(argv: list) -> int:
    as_json = "--json" in argv
    args = [a for a in argv if a != "--json"]
    if "-h" in args or "--help" in args:
        print("usage: measure.py cloud-import [PATH ...] [--json]\n"
              "PATH is a .to-cloud/<session-id>.json file or a directory of them (default: ./.to-cloud,\n"
              "which also reads .to-cloud/checkpoints/*.json).\n"
              "Rows go to trends.db tables cloud_session_summaries and cloud_checkpoints, separate from\n"
              "provider-reported session_log data. Local checkpoint files are never touched.")
        return 0
    paths = args or [".to-cloud"]
    if not _collect(paths):
        print("cloud-import: no summary files found in %s" % ", ".join(paths), file=sys.stderr)
        return 1
    try:
        report = import_files(paths)
    except (OSError, sqlite3.Error) as exc:
        print("cloud-import: %s" % exc, file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print("Imported %d cloud session summar%s and %d checkpoint%s into %s" % (
            len(report["imported"]), "y" if len(report["imported"]) == 1 else "ies",
            len(report["checkpoints_imported"]), "" if len(report["checkpoints_imported"]) == 1 else "s", report["db"]))
        if report["unchanged"]:
            print("Already up to date: %d" % len(report["unchanged"]))
        for s in report["skipped"]:
            print("Skipped %s: %s" % (s["file"], s["reason"]))
        print("Label: " + report["label"])
    ok = report["imported"] or report["checkpoints_imported"] or report["unchanged"]
    return 0 if not report["skipped"] or ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
