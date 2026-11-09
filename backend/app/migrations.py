"""Versioned schema changes, so existing data survives app updates.

The version lives in SQLite's own header (`PRAGMA user_version`); no extra
table, no extra tooling. On startup every step after the database's version
runs once, in order, each in its own transaction.

To change the schema:
  1. change the model in models.py
  2. append a step below that makes the same change to an existing database,
     e.g. `conn.exec_driver_sql("ALTER TABLE accounts ADD COLUMN colour TEXT")`
  3. never edit or reorder a step that has shipped

A database already at a newer version than this code is refused rather than
touched: that only happens when an older build is started on newer data.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import Connection, Engine, inspect

from .db import Base

log = logging.getLogger("uvicorn.error")


def _v1_baseline(conn: Connection) -> None:
    """The schema as of the first versioned release. Also adopts databases
    created before versioning existed: create_all only adds missing tables."""
    from . import models  # noqa: F401  (registers the mappers)

    Base.metadata.create_all(conn)


def _v2_connection_credentials(conn: Connection) -> None:
    """Per-bank links (TrueLayer) keep a token, an expiry and some details.

    Only adds what's missing: a database created by this build's baseline
    already has the columns."""
    existing = {col["name"] for col in inspect(conn).get_columns("connections")}
    for name, ddl in (
        ("secret", "TEXT"),
        ("expires_at", "DATETIME"),
        ("meta", "JSON"),
    ):
        if name not in existing:
            conn.exec_driver_sql(f"ALTER TABLE connections ADD COLUMN {name} {ddl}")


def _v3_payment_type(conn: Connection) -> None:
    """What kind of payment each transaction was (card, standing order, ...).
    Existing rows stay empty until their bank sends them again."""
    inspector = inspect(conn)
    if "transactions" not in inspector.get_table_names():
        return
    if "payment_type" not in {col["name"] for col in inspector.get_columns("transactions")}:
        conn.exec_driver_sql("ALTER TABLE transactions ADD COLUMN payment_type VARCHAR(16)")


def _v4_account_date_index(conn: Connection) -> None:
    """Finds each account's newest transaction without a scan."""
    if "transactions" not in inspect(conn).get_table_names():
        return
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_transactions_account_booked ON transactions (account_id, booked_on)"
    )


STEPS: list[Callable[[Connection], None]] = [
    _v1_baseline,
    _v2_connection_credentials,
    _v3_payment_type,
    _v4_account_date_index,
]
LATEST = len(STEPS)


class SchemaTooNewError(RuntimeError):
    pass


def current_version(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(conn.exec_driver_sql("PRAGMA user_version").scalar() or 0)


def migrate(engine: Engine) -> int:
    if engine.dialect.name != "sqlite":
        _v1_baseline_on(engine)
        return LATEST

    version = current_version(engine)
    if version > LATEST:
        raise SchemaTooNewError(
            f"The database is at schema version {version}, but this build only knows "
            f"up to {LATEST}. Install the newer version of the app, or restore a backup."
        )
    if version == LATEST:
        return version

    has_data = bool(inspect(engine).get_table_names())
    db_file = engine.url.database
    if has_data and db_file and db_file != ":memory:":
        from .services import backup

        saved = backup.backup_now(label=f"before-v{LATEST}", db=Path(db_file).resolve())
        if saved:
            log.info("Backed up the database to %s before upgrading its schema", saved)

    for number in range(version + 1, LATEST + 1):
        with engine.begin() as conn:
            STEPS[number - 1](conn)
            conn.exec_driver_sql(f"PRAGMA user_version = {number}")
        log.info("Database schema is now at version %d", number)
    return LATEST


def _v1_baseline_on(engine: Engine) -> None:
    with engine.begin() as conn:
        _v1_baseline(conn)
