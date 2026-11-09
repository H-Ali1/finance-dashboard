"""Migrations, backups, recovery from a damaged database, and sync retries."""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from app import migrations
from app.config import settings
from app.db import Base
from app.services import backup, scheduler


def _engine(path: Path):
    return create_engine(f"sqlite:///{path.as_posix()}")


def _good_db(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (42)")
    conn.commit()
    conn.close()
    return path


# --- migrations ---------------------------------------------------------------
def test_fresh_database_gets_latest_schema(tmp_path):
    engine = _engine(tmp_path / "fresh.db")
    assert migrations.migrate(engine) == migrations.LATEST
    assert migrations.current_version(engine) == migrations.LATEST
    assert {"accounts", "transactions", "connections"} <= set(inspect(engine).get_table_names())


def test_unversioned_database_is_adopted_and_backed_up_first(tmp_path):
    db = tmp_path / "legacy" / "finance.db"
    db.parent.mkdir()
    engine = _engine(db)
    from app import models  # noqa: F401

    Base.metadata.create_all(engine)  # how databases were made before versioning
    assert migrations.current_version(engine) == 0

    assert migrations.migrate(engine) == migrations.LATEST
    saved = list((db.parent / "backups").glob("finance-before-v*.db"))
    assert len(saved) == 1 and backup.check(saved[0]) is None


def test_version_1_database_gains_the_bank_link_columns(tmp_path):
    """A database from 1.1.x, whose connections table predates TrueLayer."""
    db = tmp_path / "v1" / "finance.db"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE connections (id INTEGER PRIMARY KEY, provider VARCHAR(32), institution_name VARCHAR(128), "
        "status VARCHAR(32), last_synced_at DATETIME, last_error TEXT, created_at DATETIME)"
    )
    conn.execute(
        "INSERT INTO connections (provider, institution_name, status) VALUES ('starling', 'Starling', 'linked')"
    )
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    engine = _engine(db)
    assert migrations.migrate(engine) == migrations.LATEST
    columns = {c["name"] for c in inspect(engine).get_columns("connections")}
    assert {"secret", "expires_at", "meta"} <= columns
    with engine.connect() as check:
        assert check.exec_driver_sql("SELECT provider FROM connections").scalar() == "starling"


def test_upgraded_database_gains_the_account_date_index(tmp_path):
    engine = _engine(tmp_path / "idx.db")
    migrations.migrate(engine)
    indexes = {i["name"] for i in inspect(engine).get_indexes("transactions")}
    assert "ix_transactions_account_booked" in indexes


def test_database_from_a_newer_build_is_refused(tmp_path):
    engine = _engine(tmp_path / "future.db")
    with engine.begin() as conn:
        conn.exec_driver_sql(f"PRAGMA user_version = {migrations.LATEST + 1}")
    with pytest.raises(migrations.SchemaTooNewError):
        migrations.migrate(engine)


# --- backups and recovery -----------------------------------------------------
def test_backups_rotate_to_the_configured_count(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "backup_keep_days", 2)
    db = _good_db(tmp_path / "finance.db")
    folder = backup.backups_dir(db)
    folder.mkdir()
    for day in ("20260101", "20260102", "20260103"):
        (folder / f"finance-daily-{day}-030000.db").write_bytes(b"old")
    newest = backup.backup_now(db=db)
    remaining = sorted(p.name for p in folder.glob("finance-daily-*.db"))
    assert len(remaining) == 2 and newest.name in remaining
    assert "finance-daily-20260103-030000.db" in remaining


def test_healthy_wal_database_is_left_alone(tmp_path):
    db = _good_db(tmp_path / "finance.db")
    assert not Path(f"{db}-wal").exists()  # cleanly closed: no WAL side files
    assert backup.recover_if_corrupt(db) is None
    assert sqlite3.connect(db).execute("SELECT x FROM t").fetchone() == (42,)


def test_damaged_database_is_replaced_by_newest_good_backup(tmp_path):
    db = _good_db(tmp_path / "finance.db")
    backup.backup_now(db=db)
    db.write_bytes(b"this is not a database" * 100)

    notice = backup.recover_if_corrupt(db)
    assert notice and notice["restored_from"].startswith("finance-daily-")
    assert sqlite3.connect(db).execute("SELECT x FROM t").fetchone() == (42,)
    assert (tmp_path / notice["damaged_copy"]).exists()  # kept, never deleted
    assert backup.restore_notice(db)["at"] == notice["at"]


def test_damaged_database_without_backup_starts_empty_but_keeps_the_file(tmp_path):
    db = tmp_path / "finance.db"
    db.write_bytes(b"garbage" * 500)
    notice = backup.recover_if_corrupt(db)
    assert notice["restored_from"] is None
    assert not db.exists() and (tmp_path / notice["damaged_copy"]).exists()


# --- scheduler ----------------------------------------------------------------
def test_failed_sync_retries_sooner_with_backoff(seeded, monkeypatch):
    def broken(_db, **_kwargs):
        raise RuntimeError("provider unreachable")

    monkeypatch.setattr(settings, "sync_interval_hours", 6)
    monkeypatch.setattr(scheduler.sync_service, "sync_all", broken)
    monkeypatch.setitem(scheduler._state, "consecutive_failures", 0)

    waits = []
    for _ in range(3):
        before = dt.datetime.now(dt.timezone.utc)
        assert scheduler.run_sync() is False
        waits.append(round((scheduler._state["next_sync_at"] - before).total_seconds() / 60))
    assert waits == [15, 30, 60]
    assert "provider unreachable" in scheduler._state["last_error"]


def test_retry_never_waits_longer_than_the_normal_interval(seeded, monkeypatch):
    monkeypatch.setattr(settings, "sync_interval_hours", 1)
    monkeypatch.setattr(
        scheduler.sync_service, "sync_all", lambda _db, **_kwargs: (_ for _ in ()).throw(OSError("down"))
    )
    monkeypatch.setitem(scheduler._state, "consecutive_failures", 10)
    before = dt.datetime.now(dt.timezone.utc)
    scheduler.run_sync()
    assert scheduler._state["next_sync_at"] - before <= dt.timedelta(hours=1, seconds=5)


def test_successful_sync_resets_failures(seeded, monkeypatch):
    monkeypatch.setattr(settings, "sync_interval_hours", 6)
    monkeypatch.setitem(scheduler._state, "consecutive_failures", 3)
    assert scheduler.run_sync() is True
    assert scheduler._state["consecutive_failures"] == 0


def test_daily_backup_runs_once_per_day(seeded):
    from app.db import database_path

    folder = backup.backups_dir(database_path())
    for old in folder.glob("finance-daily-*.db"):
        old.unlink()
    scheduler.run_maintenance()
    scheduler.run_maintenance()
    assert len(list(folder.glob("finance-daily-*.db"))) == 1


# --- watchdog -----------------------------------------------------------------
def test_health_fails_when_background_jobs_die(client, monkeypatch):
    """The HA watchdog restarts the App on a failing health check."""
    assert client.get("/api/health").status_code == 200
    monkeypatch.setattr(scheduler, "alive", lambda: False)
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["problem"] == "background jobs stopped"


def test_scheduler_restart_never_runs_two_workers():
    """Stop + immediate start must leave exactly one worker alive."""
    import threading

    for _ in range(5):
        scheduler.stop()
        scheduler.start()
    workers = [t for t in threading.enumerate() if t.name == "background-jobs" and t.is_alive()]
    assert len(workers) == 1 and scheduler.alive()
