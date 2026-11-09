"""Accounts, manual assets (car, pension, ...) and net worth."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analytics.networth import compute_networth, networth_history, snapshot_networth
from ..db import get_db
from ..models import Account, ManualAsset
from ..money import to_major, to_minor
from ..schemas import AccountUpdate, ManualAssetUpsert
from ..services.income import INCOME_DEFAULT_KEY, INCOME_DEFAULTS
from ..services.sync import recategorise_all

router = APIRouter(prefix="/api", tags=["accounts"])


def _serialise_account(account: Account) -> dict:
    return {
        "id": account.id,
        "name": account.name,
        "institution": account.institution_name or account.provider.title(),
        "provider": account.provider,
        "kind": account.kind,
        "currency": account.currency,
        "balance": to_major(account.balance_minor),
        "available": to_major(account.available_minor) if account.available_minor is not None else None,
        "is_liability": account.is_liability,
        "include_in_networth": account.include_in_networth,
        "is_active": account.is_active,
        "flip_sign": bool((account.provider_meta or {}).get("flip_sign")),
        "income_default": (account.provider_meta or {}).get(INCOME_DEFAULT_KEY),
        "updated_at": account.balance_updated_at.isoformat() if account.balance_updated_at else None,
        "holdings": (account.provider_meta or {}).get("holdings") or [],
    }


@router.get("/accounts")
def list_accounts(include_inactive: bool = False, db: Session = Depends(get_db)) -> dict:
    stmt = select(Account).order_by(Account.institution_name, Account.name)
    if not include_inactive:
        stmt = stmt.where(Account.is_active.is_(True))
    return {"accounts": [_serialise_account(a) for a in db.scalars(stmt)]}


@router.patch("/accounts/{account_id}")
def update_account(account_id: int, payload: AccountUpdate, db: Session = Depends(get_db)) -> dict:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    if payload.name is not None:
        account.name = payload.name
    if payload.kind is not None:
        account.kind = payload.kind
    if payload.include_in_networth is not None:
        account.include_in_networth = payload.include_in_networth
    if payload.is_active is not None:
        account.is_active = payload.is_active
    if payload.flip_sign is not None:
        meta = dict(account.provider_meta or {})
        meta["flip_sign"] = payload.flip_sign
        account.provider_meta = meta
    recategorise = False
    if payload.income_default is not None:
        if payload.income_default and payload.income_default not in INCOME_DEFAULTS:
            raise HTTPException(status_code=400, detail=f"income_default must be one of {sorted(INCOME_DEFAULTS)}")
        meta = dict(account.provider_meta or {})
        meta[INCOME_DEFAULT_KEY] = payload.income_default or None
        account.provider_meta = meta
        recategorise = True
    db.commit()
    if recategorise:
        recategorise_all(db)
    return _serialise_account(account)


# --- manual assets -----------------------------------------------------------
def _serialise_asset(item: ManualAsset) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "kind": item.kind,
        "category": item.category,
        "value": to_major(item.value_minor),
        "currency": item.currency,
        "annual_change_pct": item.annual_change_pct,
        "notes": item.notes,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


@router.get("/assets")
def list_assets(db: Session = Depends(get_db)) -> dict:
    items = db.scalars(select(ManualAsset).order_by(ManualAsset.kind, ManualAsset.name))
    return {"assets": [_serialise_asset(i) for i in items]}


@router.post("/assets")
def create_asset(payload: ManualAssetUpsert, db: Session = Depends(get_db)) -> dict:
    item = ManualAsset(
        name=payload.name,
        kind=payload.kind,
        category=payload.category,
        value_minor=to_minor(payload.value),
        currency=payload.currency,
        annual_change_pct=payload.annual_change_pct,
        notes=payload.notes,
    )
    db.add(item)
    db.commit()
    snapshot_networth(db)
    return _serialise_asset(item)


@router.put("/assets/{asset_id}")
def update_asset(asset_id: int, payload: ManualAssetUpsert, db: Session = Depends(get_db)) -> dict:
    item = db.get(ManualAsset, asset_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    item.name = payload.name
    item.kind = payload.kind
    item.category = payload.category
    item.value_minor = to_minor(payload.value)
    item.currency = payload.currency
    item.annual_change_pct = payload.annual_change_pct
    item.notes = payload.notes
    db.commit()
    snapshot_networth(db)
    return _serialise_asset(item)


@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: int, db: Session = Depends(get_db)) -> dict:
    item = db.get(ManualAsset, asset_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    db.delete(item)
    db.commit()
    snapshot_networth(db)
    return {"deleted": asset_id}


# --- net worth ---------------------------------------------------------------
@router.get("/networth")
def networth(db: Session = Depends(get_db)) -> dict:
    data = compute_networth(db)
    data.pop("_minor", None)
    return data


@router.get("/networth/history")
def networth_series(days: int = 365, db: Session = Depends(get_db)) -> dict:
    return networth_history(db, days)


@router.post("/networth/snapshot")
def take_snapshot(db: Session = Depends(get_db)) -> dict:
    snapshot = snapshot_networth(db)
    return {"date": snapshot.taken_on.isoformat(), "net": to_major(snapshot.net_minor)}
