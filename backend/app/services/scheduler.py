"""Background jobs: scheduled sync, daily backup, database housekeeping.

Runs in a daemon thread inside the API process, so there is nothing extra to
deploy: no cron, no second container. It wakes every few minutes and does
whatever is due:

- Sync, every `sync_interval_hours`. Same as pressing "Sync everything": pull
  from every configured provider, match transfers, record the day's net worth.
  A failed sync (provider down, network blip) is retried after 15 minutes,
  then 30, 60, ... up to the normal interval, instead of waiting a full cycle.
- Once a day: a backup of the database, then SQLite's own housekeeping.

Nothing here can stop the thread: every job is wrapped, logged and retried on
the next wake-up.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading

from ..config import settings
from ..db import SessionLocal
from . import backup
from . import sync as sync_service

# uvicorn configures this logger, so messages land in the App's Log tab.
log = logging.getLogger("uvicorn.error")

FIRST_RUN_DELAY_SECONDS = 60  # let the server finish starting
TICK_SECONDS = 300
RETRY_FIRST = dt.timedelta(minutes=15)

# Each worker thread gets its own stop signal, so stopping one can never be
# undone by a quick restart, and two workers can never run side by side.
_stop: threading.Event | None = None
_thread: threading.Thread | None = None
_lifecycle = threading.Lock()
_state: dict = {
    "running": False,
    "last_sync_at": None,
    "last_sync_ok": None,
    "last_error": None,
    "consecutive_failures": 0,
    "next_sync_at": None,
    "last_backup": None,
}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def status() -> dict:
    out = dict(_state)
    for key in ("last_sync_at", "next_sync_at"):
        if out[key] is not None:
            out[key] = out[key].isoformat(timespec="seconds")
    return out


def _interval() -> dt.timedelta | None:
    hours = settings.sync_interval_hours
    return dt.timedelta(hours=hours) if hours > 0 else None


def run_sync() -> bool:
    """One scheduled sync. Returns True when every provider synced cleanly."""
    db = SessionLocal()
    try:
        result = sync_service.sync_all(db, unattended=True)
        errors = [e.get("error") for e in result["errors"]]
        ok = not errors
        _state["last_error"] = "; ".join(errors) if errors else None
        log.info(
            "Scheduled sync: %d provider(s), %d transfer pair(s)%s",
            len(result["results"]),
            result["transfer_pairs"],
            f", errors: {_state['last_error']}" if errors else "",
        )
    except Exception as exc:  # a bug or a database problem; keep the schedule alive
        log.exception("Scheduled sync failed")
        _state["last_error"] = f"{type(exc).__name__}: {exc}"
        ok = False
    finally:
        db.close()

    now = _now()
    interval = _interval() or dt.timedelta(hours=6)
    _state["last_sync_at"] = now
    _state["last_sync_ok"] = ok
    if ok:
        _state["consecutive_failures"] = 0
        _state["next_sync_at"] = now + interval
    else:
        _state["consecutive_failures"] += 1
        retry = RETRY_FIRST * (2 ** (_state["consecutive_failures"] - 1))
        _state["next_sync_at"] = now + min(retry, interval)
        log.warning("Next sync attempt at %s", _state["next_sync_at"].isoformat(timespec="minutes"))
    return ok


def run_maintenance() -> None:
    """Daily backup and housekeeping, if today's hasn't happened yet."""
    try:
        if backup.daily_backup_due():
            saved = backup.backup_now()
            if saved:
                _state["last_backup"] = saved.name
                log.info("Daily backup saved: %s", saved.name)
            db = SessionLocal()
            try:
                sync_service.prune_sync_logs(db)
            finally:
                db.close()
            backup.optimise()
    except Exception:
        log.exception("Daily backup/housekeeping failed; will retry")


def _tick() -> None:
    if _interval() is not None:
        due = _state["next_sync_at"]
        if due is None or _now() >= due:
            run_sync()
    run_maintenance()


def _loop(stop: threading.Event) -> None:
    if stop.wait(FIRST_RUN_DELAY_SECONDS):
        return
    while not stop.is_set():
        try:
            _tick()
        except Exception:  # belt and braces: the loop itself must never die
            log.exception("Background job loop error")
        if stop.wait(TICK_SECONDS):
            return


def start() -> None:
    global _thread, _stop
    with _lifecycle:
        if _thread is not None and _thread.is_alive():
            return
        _stop = threading.Event()
        _thread = threading.Thread(target=_loop, args=(_stop,), name="background-jobs", daemon=True)
        _thread.start()
        _state["running"] = True
    if _interval() is None:
        log.info("Scheduled sync is off (sync_interval_hours = 0); daily backups still run")
    else:
        log.info("Scheduled sync every %g hour(s); daily backups on", settings.sync_interval_hours)


def stop(wait_seconds: float = 5) -> None:
    """Signal the worker and give an in-flight job a moment to finish."""
    with _lifecycle:
        thread, signal = _thread, _stop
        if signal is not None:
            signal.set()
        _state["running"] = False
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=wait_seconds)


def alive() -> bool:
    return _thread is not None and _thread.is_alive()
