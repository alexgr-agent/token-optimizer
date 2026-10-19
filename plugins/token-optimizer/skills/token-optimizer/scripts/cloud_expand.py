#!/usr/bin/env python3
"""Token Optimizer cloud kit: expand-only stand-in for ``measure.py``.

``cloud-init`` copies this file to ``.claude/to-cloud/engine/measure.py``.

When the bundled Bash hook compresses output it tells the model to read the saved
original with ``python3 <engine dir>/measure.py expand <id>``. The engine finds
``measure.py`` next to its own modules, so in the repo kit that path has to exist
and has to work. This file is that command and nothing else: it prints an archived
result, or lists them. It never writes anything.

    measure.py expand <id> [--session <session-id>]
    measure.py expand --list [--session <session-id>]

Standard library only. Keep ``default_data_dir`` identical to cloud_hook.py.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile

_ID_RE = re.compile(r"^[A-Za-z0-9_-]+\Z")


def default_data_dir() -> str:
    override = os.environ.get("TOKEN_OPTIMIZER_SNAPSHOT_DIR", "").strip()
    if override:
        return os.path.expanduser(override)
    base = "/tmp" if os.name == "posix" else tempfile.gettempdir()
    uid = os.getuid() if hasattr(os, "getuid") else 0
    return os.path.join(base, "token-optimizer-cloud-%d" % uid)


def _sessions(root: str, only: str | None) -> list:
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        path = os.path.join(root, name)
        if os.path.islink(path) or not os.path.isdir(path):
            continue
        if only and name != re.sub(r"[^A-Za-z0-9_-]", "_", only)[:96]:
            continue
        out.append(path)
    return out


def expand(argv: list) -> int:
    root = os.path.join(default_data_dir(), "tool-archive")
    session = None
    rest = list(argv)
    if "--session" in rest:
        i = rest.index("--session")
        if i + 1 >= len(rest):
            print("[Error] --session needs a value", file=sys.stderr)
            return 1
        session = rest[i + 1]
        del rest[i:i + 2]
    if rest == ["--list"]:
        total = 0
        for sd in _sessions(root, session):
            manifest = os.path.join(sd, "manifest.jsonl")
            try:
                lines = open(manifest, encoding="utf-8").read().splitlines()
            except OSError:
                continue
            entries = []
            for line in lines:
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    continue
            if entries:
                print("\n  Session: %s (%d archived)" % (os.path.basename(sd), len(entries)))
                for e in entries:
                    print("    %-30s %8s chars  %s" % (str(e.get("tool_name", "?"))[:30], e.get("chars", "?"),
                                                       str(e.get("tool_use_id", "?"))[:64]))
                    total += 1
        print("\n  Total: %d archived results\n" % total if total else "[Tool Archive] No archived results found.")
        return 0
    if len(rest) != 1:
        print("usage: measure.py expand <id> [--session <session-id>] | expand --list", file=sys.stderr)
        return 1
    key = rest[0][:-1] if rest[0].endswith("]") else rest[0]
    if not _ID_RE.match(key):
        print("[Error] Invalid tool_use_id format.", file=sys.stderr)
        return 1
    for sd in _sessions(root, session):
        entry = os.path.join(sd, key + ".json")
        if os.path.isfile(entry) and not os.path.islink(entry):
            try:
                with open(entry, encoding="utf-8") as fh:
                    response = json.load(fh).get("response", "")
            except (OSError, ValueError) as exc:
                print("[Error] Failed to read archived result: %s" % type(exc).__name__, file=sys.stderr)
                return 1
            if response:
                sys.stdout.write(response if isinstance(response, str) else json.dumps(response))
                sys.stdout.flush()
                return 0
            print("[Error] Archived entry found but response is empty", file=sys.stderr)
            return 1
    print("[Error] Tool result not found: %s" % key, file=sys.stderr)
    return 1


def main(argv: list | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "expand":
        return expand(argv[1:])
    print("This is the Token Optimizer cloud kit's expand-only helper. Use: measure.py expand <id>", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
