"""Unusual transactions: outliers within a category, first-time big merchants,
and probable double charges."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from sqlalchemy.orm import Session

from ..money import to_major
from .common import account_map, is_spend, load_transactions, robust_stats
from .recurring import detect_recurring, recurring_keys

BASELINE_DAYS = 365
RECENT_DAYS = 45
# Below this, "unusual" is just noise.
MIN_FLAG_MINOR = 3000  # £30
Z_THRESHOLD = 4.0


def _score(amount: float, med: float, mad: float) -> float:
    # 1.4826 rescales MAD so the threshold reads like a normal-distribution z.
    scale = mad * 1.4826 or max(med * 0.3, 1.0)
    return (amount - med) / scale


def detect_anomalies(db: Session, recent_days: int = RECENT_DAYS) -> list[dict]:
    baseline_start = dt.date.today() - dt.timedelta(days=BASELINE_DAYS)
    recent_start = dt.date.today() - dt.timedelta(days=recent_days)
    txns = [t for t in load_transactions(db, start=baseline_start) if is_spend(t)]
    accounts = account_map(db)
    # Rent is much larger than your average "bills" transaction every single
    # month — expected, so it must not be reported as an anomaly.
    scheduled = recurring_keys(detect_recurring(db))

    by_category: dict[str, list[float]] = defaultdict(list)
    seen_merchants: dict[str, dt.date] = {}
    for txn in txns:
        by_category[txn.category].append(float(-txn.amount_minor))
        key = txn.merchant_key or txn.merchant.lower()
        if key and (key not in seen_merchants or txn.booked_on < seen_merchants[key]):
            seen_merchants[key] = txn.booked_on

    stats = {cat: robust_stats(vals) for cat, vals in by_category.items() if len(vals) >= 6}

    out: list[dict] = []
    for txn in txns:
        if txn.booked_on < recent_start:
            continue
        amount = float(-txn.amount_minor)
        if amount < MIN_FLAG_MINOR:
            continue
        key = txn.merchant_key or txn.merchant.lower()
        if key in scheduled:
            continue

        reasons = []
        severity = "info"
        med, mad = stats.get(txn.category, (0.0, 0.0))
        z = _score(amount, med, mad) if med else 0.0
        if med and z >= Z_THRESHOLD:
            reasons.append(
                f"{to_major(int(amount)):,.2f} is well above your typical "
                f"{txn.category.lower()} spend of £{to_major(int(med)):,.2f}"
            )
            severity = "warning"

        if seen_merchants.get(key) == txn.booked_on and amount >= 10000:  # £100+
            reasons.append("first time you've paid this merchant")

        if not reasons:
            continue

        account = accounts.get(txn.account_id)
        out.append(
            {
                "id": txn.id,
                "date": txn.booked_on.isoformat(),
                "merchant": txn.merchant,
                "description": txn.description,
                "category": txn.category,
                "amount": to_major(-txn.amount_minor),
                "account": account.name if account else "",
                "z_score": round(z, 1),
                "severity": severity,
                "reasons": reasons,
            }
        )

    out.sort(key=lambda r: r["amount"], reverse=True)
    return out


def detect_double_charges(db: Session, window_days: int = 3) -> list[dict]:
    """Same merchant, same amount, same account, within a few days."""
    start = dt.date.today() - dt.timedelta(days=120)
    txns = [t for t in load_transactions(db, start=start) if is_spend(t)]
    accounts = account_map(db)

    groups: dict[tuple, list] = defaultdict(list)
    for txn in txns:
        key = (txn.account_id, txn.merchant_key or txn.merchant.lower(), txn.amount_minor)
        groups[key].append(txn)

    out = []
    for (account_id, _merchant, amount), rows in groups.items():
        if len(rows) < 2 or -amount < 1000:  # ignore sub-£10 repeats
            continue
        rows.sort(key=lambda t: t.booked_on)
        for first, second in zip(rows, rows[1:]):
            if (second.booked_on - first.booked_on).days <= window_days:
                account = accounts.get(account_id)
                out.append(
                    {
                        "merchant": first.merchant,
                        "amount": to_major(-amount),
                        "dates": [first.booked_on.isoformat(), second.booked_on.isoformat()],
                        "account": account.name if account else "",
                        "transaction_ids": [first.id, second.id],
                    }
                )
    return sorted(out, key=lambda r: r["dates"][-1], reverse=True)
