"""Automatic, encrypted backups of everything Custodian keeps: the main database and every tool's
database (memories, chats, notes, reminders, files you added), as one file.

Every day at BACKUP_TIME (default 03:30) the scheduler writes custodian-<when>.cbk into BACKUP_DIR
(default data/backups) and keeps the newest BACKUP_KEEP (default 14). Each file is a zip of
consistent copies (SQLite's backup API, safe while the server runs), encrypted as a whole with
APP_SECRET_KEY - so a backup in another folder, a USB drive or a synced cloud folder can't be read
without that key. Keep a copy of the key somewhere other than this PC (a password manager):
without it no backup can be restored, and with it every one can.

    python -m app.cli backup [folder]      one now
    python -m app.cli restore <file>       with the server stopped: the data now in use is moved
                                           aside to data/before-restore-<when>, never deleted
"""

from __future__ import annotations

import io
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .config import BACKEND_DIR, get_settings
from .crypto import decrypt_bytes, encrypt_bytes

log = logging.getLogger(__name__)

SUFFIX = ".cbk"
NAME = re.compile(r"^custodian-(\d{8}T\d{6})(?:\.\d{6})?Z\.cbk$")  # microseconds: two in one second don't collide
MANIFEST = "custodian-backup.txt"
_lock = threading.Lock()


class BackupError(RuntimeError):
    """The message is safe to show."""


def folder() -> Path:
    raw = os.getenv("BACKUP_DIR", "").strip()
    if not raw:
        return get_settings().data_dir / "backups"
    path = Path(raw).expanduser()
    return path if path.is_absolute() else BACKEND_DIR / path


def keep() -> int:
    try:
        return max(1, int(os.getenv("BACKUP_KEEP", "14")))
    except ValueError:
        return 14


def at() -> str:
    """When the daily one runs ("03:30"), or "" when automatic backups are off (BACKUP_TIME=off)."""
    raw = os.getenv("BACKUP_TIME", "03:30").strip().lower()
    if raw in ("", "off", "none", "0"):
        return ""
    return raw if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", raw) else "03:30"


def _databases() -> list[tuple[Path, str]]:
    """(database file, its name inside the backup) for the main database and each tool's."""
    settings = get_settings()
    found = [(settings.database_path, "custodian.db")]
    for path in sorted(settings.tool_data_dir.glob("*/*.db")):
        found.append((path, f"tools/{path.parent.name}/{path.name}"))
    return found


def _zip() -> bytes:
    buffer = io.BytesIO()
    with tempfile.TemporaryDirectory(prefix="custodian-backup-") as work, \
            zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source, name in _databases():
            if not source.exists():
                continue
            copy = Path(work) / name.replace("/", "_")
            src, dst = sqlite3.connect(source), sqlite3.connect(copy)
            try:
                src.backup(dst)  # a consistent copy, even mid-write
            finally:
                src.close()
                dst.close()
            archive.write(copy, name)
        made = datetime.now(timezone.utc).isoformat(timespec="seconds")
        archive.writestr(MANIFEST, f"Custodian backup\nmade {made}\nfiles: {', '.join(archive.namelist())}\n")
    return buffer.getvalue()


def make(dest: Path | None = None) -> dict:
    """Write one encrypted backup into `dest` (default BACKUP_DIR) and prune the oldest."""
    dest = dest or folder()
    with _lock:
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BackupError(f"The backup folder {dest} can't be used ({exc.strerror or exc}).") from None
        when = datetime.now(timezone.utc)
        target = dest / f"custodian-{when:%Y%m%dT%H%M%S.%f}Z{SUFFIX}"
        sealed = encrypt_bytes(_zip())
        partial = target.with_suffix(".partial")
        partial.write_bytes(sealed)
        os.replace(partial, target)  # a half-written backup is never mistaken for one
        pruned = prune(dest)
    log.info("backup written: %s (%d bytes, %d old ones removed)", target, len(sealed), len(pruned))
    return {"file": str(target), "bytes": len(sealed), "made": when.isoformat(timespec="seconds"), "removed": len(pruned)}


def prune(dest: Path) -> list[Path]:
    backups = sorted((p for p in dest.glob(f"custodian-*{SUFFIX}") if NAME.match(p.name)), key=lambda p: p.name)
    old = backups[:-keep()] if len(backups) > keep() else []
    for path in old:
        path.unlink(missing_ok=True)
    return old


def listing(dest: Path | None = None) -> list[dict]:
    dest = dest or folder()
    out = []
    for path in sorted(dest.glob(f"custodian-*{SUFFIX}"), key=lambda p: p.name, reverse=True):
        match = NAME.match(path.name)
        if match:
            made = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
            out.append({"name": path.name, "bytes": path.stat().st_size, "made": made.isoformat(timespec="seconds")})
    return out


def status() -> dict:
    found = listing()
    return {"folder": str(folder()), "time": at(), "keep": keep(), "count": len(found),
            "last": found[0] if found else None, "backups": found[:keep()]}


def run_daily() -> None:
    """The scheduler's job: one backup, logged when it fails (tried again tomorrow)."""
    try:
        make()
    except Exception:  # noqa: BLE001 - a backup failing mustn't take the scheduler down
        log.exception("the daily backup failed")


def open_backup(path: Path) -> zipfile.ZipFile:
    """The backup's contents, decrypted with APP_SECRET_KEY (any of its keys)."""
    try:
        data = decrypt_bytes(path.read_bytes())
    except FileNotFoundError:
        raise BackupError(f"There's no backup at {path}.") from None
    except Exception:  # noqa: BLE001 - InvalidToken: another key, or not a backup
        raise BackupError("That file can't be opened with this APP_SECRET_KEY: it's another key's backup, "
                          "or not a Custodian backup.") from None
    archive = zipfile.ZipFile(io.BytesIO(data))
    if MANIFEST not in archive.namelist() or "custodian.db" not in archive.namelist():
        raise BackupError("That isn't a Custodian backup.")
    return archive


def restore(path: Path) -> dict:
    """Put a backup's databases in place of the ones in use (run with the server stopped). What
    was there is moved aside, never deleted."""
    settings = get_settings()
    archive = open_backup(path)
    names = [n for n in archive.namelist() if n.endswith(".db")]
    if any(n.startswith("/") or ".." in Path(n).parts for n in names):
        raise BackupError("That backup has a file outside its own folder in it; it wasn't restored.")
    with tempfile.TemporaryDirectory(prefix="custodian-restore-", dir=settings.data_dir) as staging:
        for name in names:
            archive.extract(name, staging)
            check = sqlite3.connect(Path(staging) / name)
            try:
                if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise BackupError(f"{name} in that backup is damaged; nothing was changed.")
            finally:
                check.close()
        aside = settings.data_dir / f"before-restore-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
        aside.mkdir(parents=True)
        for name in names:
            live = settings.database_path if name == "custodian.db" else settings.tool_data_dir / Path(name).relative_to("tools")
            for suffix in ("", "-wal", "-shm"):
                current = Path(str(live) + suffix)
                if current.exists():
                    moved = aside / (name + suffix)
                    moved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(current), moved)
            live.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(Path(staging) / name), live)
    return {"restored": names, "previous_data": str(aside)}
