"""Private local output estimates for Devin. Never stores captured text."""
from __future__ import annotations
import contextlib

import hashlib
import hmac
import secrets
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import time
import uuid

SOURCE = 'devin_captured_output_estimate'
MAX_RECORDS = 10000
RETENTION_DAYS = 30


def home() -> Path:
    base = Path.home().absolute()
    raw = os.environ.get('TOKEN_OPTIMIZER_DEVIN_HOME')
    if raw:
        candidate = Path(raw)
    else:
        xdg = os.environ.get('XDG_CONFIG_HOME')
        candidate = Path(xdg) / 'devin' if xdg else base / '.config' / 'devin'
    if not candidate.is_absolute():
        raise OSError('Devin home must be an absolute path inside HOME')
    # A symlinked component is the first thing to name: resolving through it
    # would mask the real cause behind an 'outside HOME' error.
    no_symlinks(candidate)
    resolved = candidate.resolve()
    if not resolved.is_relative_to(base.resolve()):
        raise OSError('Devin home must be an absolute path inside HOME')
    if resolved == base.resolve() or resolved.name in ('.claude', '.codex', '.cursor', '.config'):
        raise OSError('Devin home must be a dedicated directory, not HOME or another tool home')
    if any(not ch.isprintable() or ch == '`' for ch in str(candidate)):
        raise OSError('unsupported control/markdown characters in home path')
    return candidate.resolve()


def loader_path_info() -> dict:
    """Documented loader location, not evidence that an agent loaded the skill."""
    documented = Path.home().resolve() / '.config' / 'devin'
    installed = home()
    return {
        'documented_home': str(documented),
        'installed_home': str(installed),
        'status': 'documented_default' if installed == documented else 'nondefault_discovery_unknown',
        'desktop_loading_verified': False,
    }


def data_dir() -> Path:
    return home() / 'token-optimizer'


def no_symlinks(path: Path) -> None:
    for component in [*reversed(path.absolute().parents), path.absolute()]:
        if component.is_symlink():
            raise OSError('symlink path refused')


def private_dir(path: Path) -> Path:
    """Create path and missing ancestors at mode 0700. Directories this creates
    are always owner-only; pre-existing ancestors must not be writable by
    group/other unless sticky, or another local user could rename the private
    tree out from under the adapter."""
    no_symlinks(path)
    missing = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    for ancestor in (cursor, *cursor.parents):
        info = ancestor.lstat()
        if info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX:
            raise OSError('group/other-writable ancestor directory refused: ' + str(ancestor))
    for folder in reversed(missing):
        folder.mkdir(mode=0o700)
    return path


def private_path(path: Path, *, directory=False, create=False) -> Path:
    no_symlinks(path)
    if create:
        if directory:
            private_dir(path)
        elif not path.exists():
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                os.close(fd)
            except FileExistsError:
                no_symlinks(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return path
    else:
        if not directory and not stat.S_ISREG(info.st_mode):
            raise OSError('regular state file required: ' + str(path))
        if directory != stat.S_ISDIR(info.st_mode):
            raise OSError('unexpected state type: ' + str(path))
        if info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1 and not directory:
            raise OSError('adapter state must be owner-only: ' + str(path))
    return path


def enable_consent() -> None:
    private_path(data_dir(), directory=True, create=True)
    target = private_path(data_dir() / 'consent.json')
    with tempfile.NamedTemporaryFile(mode='w', dir=data_dir(), delete=False) as out:
        json.dump({'devin_consent': True, 'schema': 1}, out)
        temporary = out.name
    os.replace(temporary, target)


def consent_ok() -> bool:
    try:
        private_path(data_dir(), directory=True)
        p = private_path(data_dir() / 'consent.json')
        return json.loads(p.read_text()) == {'devin_consent': True, 'schema': 1}
    except (OSError, ValueError, TypeError, RecursionError):
        return False


def record(session: str, original: str, delivered: str, outcome: str) -> None:
    from token_estimate import estimate_tokens
    private_path(data_dir(), directory=True)
    target = private_path(data_dir() / 'metrics.db', create=True)
    # SQLite sidecars inherit the DB's private mode. Refuse existing redirects.
    for suffix in ('-journal', '-wal', '-shm'):
        sidecar=Path(str(target)+suffix)
        no_symlinks(sidecar)
        try:info=sidecar.lstat()
        except FileNotFoundError:continue
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid():
            raise OSError('foreign SQLite sidecar refused')
    # 'with connect()' only commits; the connection must also be closed.
    with contextlib.closing(sqlite3.connect(target, timeout=10)) as db:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, session_key TEXT, created REAL, source TEXT, original_estimate INTEGER, delivered_estimate INTEGER, outcome TEXT)')
            db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?)',
                       (uuid.uuid4().hex, hmac.new(session_salt(), session.encode(), hashlib.sha256).hexdigest(), time.time(),
                        SOURCE, estimate_tokens(original), estimate_tokens(delivered), outcome))
            db.execute('DELETE FROM events WHERE created < ?', (time.time() - RETENTION_DAYS * 86400,))
            db.execute('DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY created DESC LIMIT -1 OFFSET ?)', (MAX_RECORDS,))


def summary() -> dict:
    result = {'source': SOURCE, 'provider_tokens': None, 'cost': None,
              'quality_score': None, 'delivery_verified': False, 'events': 0,
              'original_estimate': 0, 'delivered_estimate': 0,
              'estimated_output_reduction': 0}
    private_path(data_dir(), directory=True)
    target = private_path(data_dir() / 'metrics.db')
    if not target.exists():
        return result
    with contextlib.closing(sqlite3.connect(target.as_uri() + '?mode=ro', uri=True)) as db:
        row = db.execute('SELECT COUNT(*), COALESCE(SUM(original_estimate),0), COALESCE(SUM(delivered_estimate),0) FROM events WHERE created >= ?', (time.time() - RETENTION_DAYS * 86400,)).fetchone()
    result.update(events=row[0], original_estimate=row[1], delivered_estimate=row[2],
                  estimated_output_reduction=row[1] - row[2])
    return result


def session_salt() -> bytes:
    p = private_path(data_dir() / 'session-salt', create=True)
    import fcntl
    with p.open('r+b') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        content = handle.read()
        if not content:
            content = secrets.token_bytes(32)
            handle.write(content); handle.flush()
        if len(content) != 32:
            raise OSError('invalid session salt')
        return content


def prepare_capture() -> Path:
    if not consent_ok():
        raise OSError('local data consent required')
    root = private_path(data_dir() / 'captures', directory=True, create=True)
    result = root / ('capture-' + uuid.uuid4().hex)
    result.mkdir(mode=0o700)
    for name in ('stdout', 'stderr'):
        private_path(result / name, create=True)
    provenance = {}
    for name in ('stdout', 'stderr'):
        info = (result / name).stat()
        provenance[name] = [info.st_dev, info.st_ino]
    p = private_path(result / '.prepared')
    # Atomic publish: a kill mid-write must never leave a torn .prepared.
    with tempfile.NamedTemporaryFile(mode='w', dir=result, delete=False) as out:
        out.write(json.dumps(provenance))
        out.flush()
        os.fsync(out.fileno())
        temporary = out.name
    os.replace(temporary, p)
    return result


def directory_fd(path: Path) -> int:
    """Open absolute directory components without following any symlink."""
    if not path.is_absolute():
        raise OSError('absolute directory required')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open('/', flags)
    try:
        for component in path.parts[1:]:
            if component in ('.', '..'):
                raise OSError('relative directory component refused')
            next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd); fd = next_fd
        return fd
    except Exception:
        os.close(fd); raise


def delete_capture(directory: Path) -> None:
    """Unlink a capture's fixed leaves and remove the capture directory,
    contained to already-opened descriptors so a renamed or swapped directory
    cannot redirect deletion outside the captures root. Foreign or unsafe
    content keeps the directory in place; no path string is followed once the
    descriptors are open."""
    root = data_dir() / 'captures'
    if not isinstance(directory, Path) or directory.parent != root or not re.fullmatch(r'capture-[0-9a-f]{32}', directory.name):
        raise OSError('adapter-prepared capture required')
    # Lexical pre-check only; the descriptor operations below are the boundary.
    no_symlinks(directory)
    for name in ('stdout', 'stderr', '.prepared', 'complete.json'):
        no_symlinks(directory / name)
    root_fd = directory_fd(root)
    capture_fd = None
    try:
        try:
            capture_fd = os.open(directory.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        except OSError:
            return  # Missing or swapped away before open: nothing here is ours.
        info = os.fstat(capture_fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise OSError('capture directory must be private')
        for name in ('stdout', 'stderr', '.prepared', 'complete.json'):
            try:
                leaf = os.stat(name, dir_fd=capture_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISREG(leaf.st_mode):
                os.unlink(name, dir_fd=capture_fd)
        # Skip the rmdir when the name no longer points at the opened directory.
        # rmdir only ever removes an EMPTY directory, so a swap between this
        # check and the call cannot delete anyone's files.
        try:
            current = os.stat(directory.name, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            return
        try:
            os.rmdir(directory.name, dir_fd=root_fd)
        except OSError:
            pass
    finally:
        if capture_fd is not None:
            os.close(capture_fd)
        os.close(root_fd)
