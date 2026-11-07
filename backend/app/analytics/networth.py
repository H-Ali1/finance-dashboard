"""Net worth: connected balances + Trading 212 + manual assets (car, pension) − debts."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Account, ManualAsset, NetWorthSnapshot
from ..money import to_major

LIQUID_KINDS = {"current", "savings"}


def compute_networth(db: Session) -> dict:
    accounts = list(
        db.scalars(select(Account).where(Account.is_active.is_(True), Account.include_in_networth.is_(True)))
    )
    manual = list(db.scalars(select(ManualAsset)))

    assets: list[dict] = []
    liabilities: list[dict] = []

    for account in accounts:
        entry = {
            "id": f"account:{account.id}",
            "name": account.name,
            "institution": account.institution_name or account.provider.title(),
            "kind": account.kind,
            "value": to_major(abs(account.balance_minor)),
            "value_minor": abs(account.balance_minor),
            "source": account.provider,
            "updated_at": account.balance_updated_at.isoformat() if account.balance_updated_at else None,
        }
        # An overdrawn current account is a debt, not a negative asset.
        if account.is_liability or account.balance_minor < 0:
            liabilities.append(entry)
        else:
            assets.append(entry)

    for item in manual:
        entry = {
            "id": f"manual:{item.id}",
            "name": item.name,
            "institution": "Manual",
            "kind": item.category,
            "value": to_major(abs(item.value_minor)),
            "value_minor": abs(item.value_minor),
            "source": "manual",
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        }
        (liabilities if item.kind == "liability" else assets).append(entry)

    assets.sort(key=lambda e: e["value_minor"], reverse=True)
    liabilities.sort(key=lambda e: e["value_minor"], reverse=True)

    assets_minor = sum(e["value_minor"] for e in assets)
    liabilities_minor = sum(e["value_minor"] for e in liabilities)

    liquid_minor = sum(
        a.balance_minor for a in accounts if a.kind in LIQUID_KINDS and a.balance_minor > 0
    )
    investments_minor = sum(a.balance_minor for a in accounts if a.kind == "investment")
    pension_minor = sum(m.value_minor for m in manual if m.category == "pension" and m.kind == "asset")
    card_debt_minor = sum(abs(a.balance_minor) for a in accounts if a.kind == "credit_card")

    return {
        "as_of": dt.date.today().isoformat(),
        "assets": to_major(assets_minor),
        "liabilities": to_major(liabilities_minor),
        "net": to_major(assets_minor - liabilities_minor),
        "liquid": to_major(liquid_minor),
        "investments": to_major(investments_minor),
        "pension": to_major(pension_minor),
        "card_debt": to_major(card_debt_minor),
        "asset_items": assets,
        "liability_items": liabilities,
        "_minor": {
            "assets": assets_minor,
            "liabilities": liabilities_minor,
            "net": assets_minor - liabilities_minor,
            "liquid": liquid_minor,
            "card_debt": card_debt_minor,
        },
    }


def snapshot_networth(db: Session) -> NetWorthSnapshot:
    """One row per day; syncing twice in a day just updates today's row."""
    data = compute_networth(db)
    today = dt.date.today()
    snapshot = db.scalar(select(NetWorthSnapshot).where(NetWorthSnapshot.taken_on == today))
    if snapshot is None:
        snapshot = NetWorthSnapshot(taken_on=today)
        db.add(snapshot)

    snapshot.assets_minor = data["_minor"]["assets"]
    snapshot.liabilities_minor = data["_minor"]["liabilities"]
    snapshot.net_minor = data["_minor"]["net"]
    snapshot.breakdown = {
        "liquid": data["liquid"],
        "investments": data["investments"],
        "pension": data["pension"],
        "card_debt": data["card_debt"],
    }
    db.commit()
    return snapshot


def networth_history(db: Session, days: int = 365) -> dict:
    since = dt.date.today() - dt.timedelta(days=days)
    rows = list(
        db.scalars(
            select(NetWorthSnapshot)
            .where(NetWorthSnapshot.taken_on >= since)
            .order_by(NetWorthSnapshot.taken_on)
        )
    )
    series = [
        {
            "date": r.taken_on.isoformat(),
            "net": to_major(r.net_minor),
            "assets": to_major(r.assets_minor),
            "liabilities": to_major(r.liabilities_minor),
            **(r.breakdown or {}),
        }
        for r in rows
    ]
    change = None
    if len(series) >= 2:
        change = {
            "since": series[0]["date"],
            "amount": round(series[-1]["net"] - series[0]["net"], 2),
            "pct": round((series[-1]["net"] - series[0]["net"]) / abs(series[0]["net"]) * 100, 1)
            if series[0]["net"]
            else None,
        }
    return {"series": series, "change": change}
