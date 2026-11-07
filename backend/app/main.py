"""Personal finance dashboard API, and the built UI when one is present.

No data leaves the machine: the only outbound calls are to the provider APIs
you configure. Run locally, the API listens on localhost and Vite serves the UI.
Packaged as a Home Assistant App, this one process serves both, behind HA's
ingress proxy and login.
"""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db, init_db
from .models import Account, Transaction
from .routers import accounts, analytics, connections, transactions
from .services import backup, scheduler
from .services import sync as sync_service

# Clients that are always allowed: the container talking to itself (Docker and
# Kubernetes health probes run inside it).
LOOPBACK = {"127.0.0.1", "::1"}
SCHEMA_VERSION: int | None = None


class _QuietHealthChecks(logging.Filter):
    """Drop access-log lines for health checks: the watchdog polls constantly."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/api/health" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_QuietHealthChecks())


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global SCHEMA_VERSION
    backup.recover_if_corrupt()
    SCHEMA_VERSION = init_db()
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(
    title="Finance Dashboard",
    description="Local budgeting and net-worth dashboard for UK personal finances.",
    version="1.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin, "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Compress JSON and the UI bundle; matters most over a phone connection.
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def only_allowed_clients(request: Request, call_next):
    """Refuse anyone but the configured clients (in HA: the ingress proxy).

    The app has no login of its own. Under Home Assistant, every legitimate
    request arrives through the Supervisor's ingress proxy after HA has checked
    who you are, so anything else is turned away.
    """
    allowed = settings.allowed_client_ip_set
    host = request.client.host if request.client else None
    # The health check is always answered: watchdogs and probes must never see
    # a 403, or they'd restart a healthy app forever. It reveals only counts
    # and sync status, nothing financial.
    is_health_check = request.url.path == "/api/health" and request.method == "GET"
    if allowed and host not in allowed and host not in LOOPBACK and not is_health_check:
        return PlainTextResponse("Forbidden", status_code=403)
    response = await call_next(request)
    # Built assets have a content hash in their name, so they never change:
    # let the browser keep them for a year and skip the request entirely.
    if request.url.path.startswith("/assets/") and response.status_code == 200:
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


app.include_router(connections.router)
app.include_router(accounts.router)
app.include_router(transactions.router)
app.include_router(analytics.router)


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    """Liveness for the HA watchdog / Docker / Kubernetes probes, plus status.

    Answers 503 when something only a restart can fix: the database stops
    answering, or the background-jobs thread has died.
    """
    try:
        txn_count = db.scalar(select(func.count()).select_from(Transaction)) or 0
        account_count = db.scalar(select(func.count()).select_from(Account)) or 0
        latest = db.scalar(select(func.max(Transaction.booked_on)))
    except Exception as exc:
        return JSONResponse({"status": "error", "problem": f"database: {exc}"}, status_code=503)
    if not scheduler.alive():
        return JSONResponse({"status": "error", "problem": "background jobs stopped"}, status_code=503)

    return {
        "status": "ok",
        "accounts": account_count,
        "transactions": txn_count,
        "latest_transaction": latest.isoformat() if latest else None,
        "providers_configured": {
            "starling": settings.starling_configured,
            "trading212": settings.trading212_configured,
        },
        "base_currency": settings.base_currency,
        "schema_version": SCHEMA_VERSION,
        "jobs": scheduler.status(),
        "restored_from_backup": backup.restore_notice(),
    }


@app.post("/api/sync")
def sync_everything(db: Session = Depends(get_db)) -> dict:
    """Pull from every configured provider, then rebuild transfers + net worth."""
    return sync_service.sync_all(db)


# --- the built UI ------------------------------------------------------------
DIST = Path(settings.frontend_dist).resolve()
INDEX = DIST / "index.html"
# HA's ingress proxy says where it mounted us, e.g. /api/hassio_ingress/<token>.
_SAFE_PREFIX = re.compile(r"^/[A-Za-z0-9/_\-.]*$")


def _index_html(request: Request) -> HTMLResponse:
    """index.html, with a <base> so relative asset and API URLs resolve under
    whatever path prefix a proxy put us behind."""
    html = INDEX.read_text(encoding="utf-8")
    prefix = request.headers.get("x-ingress-path", "").rstrip("/")
    if prefix and _SAFE_PREFIX.match(prefix):
        html = html.replace("<head>", f'<head>\n    <base href="{prefix}/" />', 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


if INDEX.is_file():
    if (DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def ui_root(request: Request) -> HTMLResponse:
        return _index_html(request)

    @app.get("/{path:path}", include_in_schema=False)
    def ui_fallback(path: str, request: Request):
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (DIST / path).resolve()
        if candidate.is_file() and candidate.is_relative_to(DIST):
            return FileResponse(candidate)
        return _index_html(request)
