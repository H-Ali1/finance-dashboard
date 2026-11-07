"""On-device database backups, and recovery from a damaged database file.

- A daily copy goes to backups/ next to the database (the last
  BACKUP_KEEP_DAYS are kept), plus one before every schema upgrade.
- At startup the database is checked. If it can't be opened or fails SQLite's
  integrity check, it's moved aside (never deleted) and the newest good backup
  is put in its place. Live feeds re-pull anything newer on the next sync,
  because each sync starts a week before the newest transaction it has.

Copies are made with SQLite's backup API, so they're consistent even while the
app is writing.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import shutil
import sqlite3
from pathlib import Path

from ..config import settings
from ..db import database_path

log = logging.getLogger("uvicorn.error")

PREFIX = "finance-"
DAILY = "daily"
NOTICE_FILE = "restored-from-backup.json"
KEEP_OTHER = 3  # pre-upgrade copies


def backups_dir(db: Path) -> Path:
    return db.parent / "backups"


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def check(path: Path) -> str | None:
    """None if the file is a healthy SQLite database, otherwise what's wrong.

    Only damage counts. "Can't open" and "locked" (OperationalError) are raised,
    not reported: replacing a healthy database because of a permissions problem
    or a busy file would throw away real data. Opened read-write on purpose — a
    read-only open of a WAL database can fail when the database is fine.
    """
    conn = sqlite3.connect(path.as_posix(), timeout=15)
    try:
        result = conn.execute("PRAGMA quick_check").fetchone()
    except sqlite3.OperationalError:
        raise
    except sqlite3.DatabaseError as exc:  # "file is not a database", "malformed", ...
        return str(exc)
    finally:
        conn.close()
    if not result or result[0] != "ok":
        return f"integrity check failed: {result[0] if result else 'no result'}"
    return None


def backup_now(label: str = DAILY, db: Path | None = None) -> Path | None:
    db = db or database_path()
    if db is None or not db.exists():
        return None
    target_dir = backups_dir(db)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{PREFIX}{label}-{_now():%Y%m%d-%H%M%S}.db"
    partial = target.with_suffix(".partial")

    src = sqlite3.connect(db.as_posix(), timeout=15)
    dst = sqlite3.connect(partial.as_posix())
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    os.replace(partial, target)
    rotate(db)
    return target


def _backups(db: Path, label: str | None = None) -> list[Path]:
    """Newest first."""
    folder = backups_dir(db)
    if not folder.is_dir():
        return []
    pattern = f"{PREFIX}{label}-*.db" if label else f"{PREFIX}*.db"
    return sorted(folder.glob(pattern), key=lambda p: p.name.rsplit("-", 2)[-2:], reverse=True)


def rotate(db: Path) -> None:
    keep_daily = max(settings.backup_keep_days, 1)
    for old in _backups(db, DAILY)[keep_daily:]:
        old.unlink(missing_ok=True)
    others = [p for p in _backups(db) if not p.name.startswith(f"{PREFIX}{DAILY}-")]
    for old in others[KEEP_OTHER:]:
        old.unlink(missing_ok=True)


def daily_backup_due(db: Path | None = None) -> bool:
    db = db or database_path()
    if db is None or not db.exists():
        return False
    today = f"{_now():%Y%m%d}"
    return not any(today in p.name for p in _backups(db, DAILY))


def latest_backup(db: Path) -> Path | None:
    return next(iter(_backups(db)), None)


def recover_if_corrupt(db: Path | None = None) -> dict | None:
    """Swap a damaged database for the newest good backup. Returns what was
    done, or None when the database was fine (or doesn't exist yet)."""
    db = db or database_path()
    if db is None or not db.exists():
        return None
    problem = check(db)
    if problem is None:
        return None

    log.error("The database at %s is damaged (%s). Recovering.", db, problem)
    stamp = f"{_now():%Y%m%d-%H%M%S}"
    corrupt = db.with_name(f"{db.name}.damaged-{stamp}")
    for suffix in ("", "-wal", "-shm"):
        part = db.with_name(db.name + suffix)
        if part.exists():
            os.replace(part, corrupt.with_name(corrupt.name + suffix))

    restored_from = None
    for candidate in _backups(db):
        if check(candidate) is None:
            shutil.copy2(candidate, db)
            restored_from = candidate
            break

    notice = {
        "at": _now().isoformat(timespec="seconds"),
        "problem": problem,
        "damaged_copy": corrupt.name,
        "restored_from": restored_from.name if restored_from else None,
    }
    (db.parent / NOTICE_FILE).write_text(json.dumps(notice, indent=2), encoding="utf-8")
    if restored_from:
        log.error("Restored the database from %s. The damaged file was kept as %s.", restored_from.name, corrupt.name)
    else:
        log.error("No good backup was found; starting with an empty database. The damaged file was kept as %s.", corrupt.name)
    return notice


def restore_notice(db: Path | None = None) -> dict | None:
    db = db or database_path()
    if db is None:
        return None
    path = db.parent / NOTICE_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def optimise(db: Path | None = None) -> None:
    """SQLite's own housekeeping: refresh query-planner statistics and fold the
    write-ahead log back into the main file."""
    db = db or database_path()
    if db is None or not db.exists():
        return
    conn = sqlite3.connect(db.as_posix(), timeout=15)
    try:
        conn.execute("PRAGMA optimize")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
