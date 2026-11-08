"""Configured providers, connecting banks through TrueLayer, and pulling fresh data."""

from __future__ import annotations

import datetime as dt
import ipaddress

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Connection, SyncLog
from ..providers import ProviderError, truelayer
from ..services import sync as sync_service

router = APIRouter(prefix="/api/connections", tags=["connections"])
truelayer_router = APIRouter(prefix="/api/truelayer", tags=["connections"])


def psu_ip(request: Request) -> str | None:
    """Your public IP address, when a proxy passed it on. Banks lift their
    four-a-day limit for requests made while you're present. Private
    addresses (on your home network) aren't sent."""
    forwarded = request.headers.get("x-forwarded-for", "")
    candidates = [part.strip() for part in forwarded.split(",") if part.strip()]
    if request.client:
        candidates.append(request.client.host)
    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.is_global:
            return str(address)
    return None


def _serialise(connection: Connection) -> dict:
    expires = connection.expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=dt.timezone.utc)
    return {
        "id": connection.id,
        "provider": connection.provider,
        "institution_name": connection.institution_name,
        "status": connection.status,
        "last_synced_at": connection.last_synced_at.isoformat() if connection.last_synced_at else None,
        "last_error": connection.last_error,
        "account_count": len(connection.accounts),
        "expires_at": expires.isoformat() if expires else None,
        "bank_id": (connection.meta or {}).get("provider_id"),
    }


@router.get("")
def list_connections(db: Session = Depends(get_db)) -> dict:
    connections = list(db.scalars(select(Connection).order_by(Connection.created_at)))
    return {
        "connections": [_serialise(c) for c in connections],
        "providers": {
            "starling": {
                "configured": settings.starling_configured,
                "how": "Create a personal access token at developer.starlingbank.com, then paste it "
                "into the App's Configuration tab in Home Assistant (or set STARLING_ACCESS_TOKEN "
                "in backend/.env when running locally)",
            },
            "trading212": {
                "configured": settings.trading212_configured,
                "how": "Trading 212 app -> Settings -> API -> Generate API key with the 'account' and "
                "'portfolio' read permissions, then paste the key and its secret into the App's "
                "Configuration tab in Home Assistant (TRADING212_API_KEY / TRADING212_API_SECRET in "
                "backend/.env when running locally). A second account (e.g. your ISA) has its own key.",
            },
            "truelayer": {
                "configured": settings.truelayer_configured,
                "how": "For HSBC, Amex and other UK banks. Sign up at console.truelayer.com, switch the "
                "app to Live, add https://console.truelayer.com/redirect-page to its Allowed redirect "
                "URIs, then paste its client ID and secret into the App's Configuration tab "
                "(TRUELAYER_CLIENT_ID / TRUELAYER_CLIENT_SECRET in backend/.env). TrueLayer doesn't "
                "support personal use, so this can stop working without notice.",
            },
        },
    }


@router.post("/{connection_id}/sync")
def sync_connection(connection_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    connection = db.get(Connection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Connection not found")

    with sync_service.SYNC_LOCK:
        if connection.provider == "starling":
            result = sync_service.sync_starling(db)
        elif connection.provider == "trading212":
            result = sync_service.sync_trading212(db)
        elif connection.provider == "truelayer":
            result = sync_service.sync_truelayer(db, connection, psu_ip=psu_ip(request))
        else:
            raise HTTPException(status_code=400, detail=f"{connection.provider} has nothing to sync")

    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
    return result


@router.delete("/{connection_id}")
def delete_connection(connection_id: int, forget_data: bool = False, db: Session = Depends(get_db)) -> dict:
    connection = db.get(Connection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Connection not found")

    if connection.provider == "truelayer":
        sync_service.disconnect_truelayer(connection)
    for account in connection.accounts:
        if forget_data:
            db.delete(account)
        else:
            account.is_active = False
            account.connection_id = None
    # Keep the sync history, detached: it points at the connection otherwise,
    # and the database refuses to delete a row something still points at.
    db.execute(update(SyncLog).where(SyncLog.connection_id == connection_id).values(connection_id=None))
    db.delete(connection)
    db.commit()
    return {"deleted": connection_id, "data_removed": forget_data}


# --- TrueLayer: connecting a bank -------------------------------------------
class LinkRequest(BaseModel):
    bank_id: str
    scopes: list[str] | None = None


class ConnectRequest(BaseModel):
    code: str


@truelayer_router.get("/banks")
def list_banks() -> dict:
    try:
        return {"banks": truelayer.banks()}
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@truelayer_router.post("/link")
def create_link(body: LinkRequest) -> dict:
    """The address that starts approving access to one bank."""
    scopes = body.scopes
    try:
        if not scopes:
            scopes = next((b["scopes"] for b in truelayer.banks() if b["provider_id"] == body.bank_id), None)
        return {"url": truelayer.auth_link(body.bank_id, scopes)}
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@truelayer_router.post("/connect")
def finish_connecting(body: ConnectRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    """Swap the code from TrueLayer's page for access, then read the bank."""
    try:
        result = sync_service.connect_truelayer(db, body.code, psu_ip=psu_ip(request))
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
    return result
