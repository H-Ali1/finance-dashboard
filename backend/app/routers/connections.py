"""Configured providers and pulling fresh data."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Connection
from ..services import sync as sync_service

router = APIRouter(prefix="/api/connections", tags=["connections"])


def _serialise(connection: Connection) -> dict:
    return {
        "id": connection.id,
        "provider": connection.provider,
        "institution_name": connection.institution_name,
        "status": connection.status,
        "last_synced_at": connection.last_synced_at.isoformat() if connection.last_synced_at else None,
        "last_error": connection.last_error,
        "account_count": len(connection.accounts),
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
        },
    }


@router.post("/{connection_id}/sync")
def sync_connection(connection_id: int, db: Session = Depends(get_db)) -> dict:
    connection = db.get(Connection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Connection not found")

    if connection.provider == "starling":
        sync_one = sync_service.sync_starling
    elif connection.provider == "trading212":
        sync_one = sync_service.sync_trading212
    else:
        raise HTTPException(status_code=400, detail=f"{connection.provider} has nothing to sync")
    with sync_service.SYNC_LOCK:
        result = sync_one(db)

    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
    return result


@router.delete("/{connection_id}")
def delete_connection(connection_id: int, forget_data: bool = False, db: Session = Depends(get_db)) -> dict:
    connection = db.get(Connection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Connection not found")

    for account in connection.accounts:
        if forget_data:
            db.delete(account)
        else:
            account.is_active = False
            account.connection_id = None
    db.delete(connection)
    db.commit()
    return {"deleted": connection_id, "data_removed": forget_data}
