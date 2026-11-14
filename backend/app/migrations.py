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


def _v5_one_rule_per_pattern(conn: Connection) -> None:
    """Older versions could save the same rule twice. Keep the latest of each
    and stop it happening again."""
    if "category_rules" not in inspect(conn).get_table_names():
        return
    conn.exec_driver_sql(
        "DELETE FROM category_rules WHERE id NOT IN "
        "(SELECT MAX(id) FROM category_rules GROUP BY field, match_type, pattern)"
    )
    conn.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_rule_pattern ON category_rules (field, match_type, pattern)"
    )


def _v6_richer_rules(conn: Connection) -> None:
    """Rules gain conditions (money in or out, amount, account, kind of
    payment) and actions (rename the merchant, leave it out of the totals).
    One rule per set of conditions now, not per pattern, so the rules table is
    rebuilt. Transactions remember who renamed or excluded them."""
    from .models import rule_signature

    inspector = inspect(conn)
    tables = inspector.get_table_names()
    if "transactions" in tables:
        existing = {col["name"] for col in inspector.get_columns("transactions")}
        for name, ddl in (
            ("merchant_source", "VARCHAR(8)"),
            ("bank_merchant", "VARCHAR(160)"),
            ("bank_merchant_key", "VARCHAR(160)"),
            ("excluded_source", "VARCHAR(8)"),
        ):
            if name not in existing:
                conn.exec_driver_sql(f"ALTER TABLE transactions ADD COLUMN {name} {ddl}")
        # Left out of the totals before rules could do it: by you, so rules leave them be.
        conn.exec_driver_sql(
            "UPDATE transactions SET excluded_source = 'manual' WHERE excluded = 1 AND excluded_source IS NULL"
        )

    if "category_rules" not in tables:
        return
    if "signature" in {col["name"] for col in inspector.get_columns("category_rules")}:
        # Created by this build's baseline: only the index step 5 added on the
        # pattern alone has to go, as rules may now share a pattern.
        conn.exec_driver_sql("DROP INDEX IF EXISTS uq_rule_pattern")
        return

    conn.exec_driver_sql(
        "CREATE TABLE category_rules_v6 ("
        "id INTEGER NOT NULL PRIMARY KEY, "
        "pattern VARCHAR(160) NOT NULL, "
        "match_type VARCHAR(16) NOT NULL, "
        "field VARCHAR(16) NOT NULL, "
        "direction VARCHAR(4), "
        "amount_min_minor INTEGER, "
        "amount_max_minor INTEGER, "
        "account_id INTEGER, "
        "payment_type VARCHAR(16), "
        "category VARCHAR(48) NOT NULL, "
        "rename_to VARCHAR(160), "
        "exclude BOOLEAN NOT NULL, "
        "priority INTEGER NOT NULL, "
        "created_at DATETIME NOT NULL, "
        "signature VARCHAR(512) NOT NULL, "
        "CONSTRAINT uq_rule_signature UNIQUE (signature))"
    )
    conn.exec_driver_sql(
        "INSERT INTO category_rules_v6 "
        "(id, pattern, match_type, field, category, exclude, priority, created_at, signature) "
        "SELECT id, COALESCE(pattern, ''), COALESCE(match_type, 'contains'), COALESCE(field, 'merchant'), "
        "COALESCE(category, ''), 0, COALESCE(priority, 100), COALESCE(created_at, CURRENT_TIMESTAMP), "
        "'pending:' || id FROM category_rules"
    )
    # Rules that only differed by a missing field are now the same rule: the
    # latest of them stays, as in step 5.
    seen: set[str] = set()
    rows = conn.exec_driver_sql("SELECT id, pattern, match_type, field FROM category_rules_v6 ORDER BY id DESC").all()
    for rule_id, pattern, match_type, field in rows:
        signature = rule_signature(pattern=pattern, match_type=match_type, field=field)
        if signature in seen:
            conn.exec_driver_sql("DELETE FROM category_rules_v6 WHERE id = ?", (rule_id,))
            continue
        seen.add(signature)
        conn.exec_driver_sql("UPDATE category_rules_v6 SET signature = ? WHERE id = ?", (signature, rule_id))
    conn.exec_driver_sql("DROP TABLE category_rules")
    conn.exec_driver_sql("ALTER TABLE category_rules_v6 RENAME TO category_rules")


STEPS: list[Callable[[Connection], None]] = [
    _v1_baseline,
    _v2_connection_credentials,
    _v3_payment_type,
    _v4_account_date_index,
    _v5_one_rule_per_pattern,
    _v6_richer_rules,
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
