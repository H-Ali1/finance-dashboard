"""SQLite engine + session plumbing."""

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from .config import settings

_url = make_url(settings.database_url)
_is_sqlite = _url.get_backend_name() == "sqlite"

engine = create_engine(
    settings.database_url,
    # timeout: how long a writer waits for another writer before giving up.
    connect_args={"check_same_thread": False, "timeout": 15} if _is_sqlite else {},
    # A fresh SQLite connection per session instead of a capped pool. FastAPI
    # closes each request's session (get_db's `finally`) on its worker-thread
    # pool; with a pool smaller than that, a burst of requests can fill every
    # worker with threads waiting for a connection, leaving none free to give
    # one back — a deadlock that ends in 30-second timeouts. Opening a SQLite
    # file costs well under a millisecond, so there's nothing worth pooling.
    poolclass=NullPool if _is_sqlite else None,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
    if _is_sqlite:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        # Safe with WAL (a power cut can lose the last commit, never corrupt the
        # file) and far fewer flushes, which matters on eMMC/SD storage.
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def database_path() -> Path | None:
    """The SQLite file on disk, or None for anything that isn't one."""
    if not _is_sqlite or not _url.database or _url.database == ":memory:":
        return None
    return Path(_url.database).resolve()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> int:
    """Bring the schema up to date. Returns the schema version now in use."""
    from . import migrations

    return migrations.migrate(engine)
