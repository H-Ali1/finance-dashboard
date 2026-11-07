"""Shared loading + date helpers for the analytics modules.

At personal-finance scale (tens of thousands of rows at most) it is clearer and
fast enough to pull transactions into Python and aggregate there, rather than
hand-writing a different GROUP BY for every view.
"""

from __future__ import annotations

import calendar
import datetime as dt
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Account, Transaction

EXCLUDED_FROM_SPEND = {"Transfers", "Savings & Investments"}


def month_key(day: dt.date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def month_bounds(key: str) -> tuple[dt.date, dt.date]:
    year, month = (int(p) for p in key.split("-"))
    last = calendar.monthrange(year, month)[1]
    return dt.date(year, month, 1), dt.date(year, month, last)


def month_keys_back(count: int, *, end: dt.date | None = None) -> list[str]:
    end = end or dt.date.today()
    keys = []
    year, month = end.year, end.month
    for _ in range(count):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(keys))


def load_transactions(
    db: Session,
    *,
    start: dt.date | None = None,
    end: dt.date | None = None,
    include_transfers: bool = False,
    include_pending: bool = True,
) -> list[Transaction]:
    stmt = select(Transaction).where(Transaction.excluded.is_(False))
    if start:
        stmt = stmt.where(Transaction.booked_on >= start)
    if end:
        stmt = stmt.where(Transaction.booked_on <= end)
    if not include_transfers:
        stmt = stmt.where(Transaction.is_transfer.is_(False))
    if not include_pending:
        stmt = stmt.where(Transaction.status == "booked")
    return list(db.scalars(stmt.order_by(Transaction.booked_on.desc())))


def is_spend(txn: Transaction) -> bool:
    return txn.amount_minor < 0 and txn.category not in EXCLUDED_FROM_SPEND


def is_income(txn: Transaction) -> bool:
    return txn.amount_minor > 0 and txn.category not in EXCLUDED_FROM_SPEND


def spend_minor(txns) -> int:
    """Positive number: how much went out."""
    return sum(-t.amount_minor for t in txns if is_spend(t))


def income_minor(txns) -> int:
    return sum(t.amount_minor for t in txns if is_income(t))


def account_map(db: Session) -> dict[int, Account]:
    return {a.id: a for a in db.scalars(select(Account))}


def robust_stats(values: list[float]) -> tuple[float, float]:
    """(median, median absolute deviation) — outlier-resistant, unlike mean/stdev."""
    if not values:
        return 0.0, 0.0
    med = median(values)
    mad = median([abs(v - med) for v in values]) or 0.0
    return med, mad


def pct_change(current: float, previous: float) -> float | None:
    if not previous:
        return None
    return round((current - previous) / abs(previous) * 100, 1)


def project_month_total(
    spent_to_date: int, elapsed: int, total_days: int, historical_month_avg: int
) -> int:
    """Estimate a full month's total from a partial month.

    Naively scaling day-2 spending by 15 says you'll spend fifteen rents this
    month. Instead the daily rate is a blend of what you've actually done so far
    and your historical rate, weighted by how much of the month has elapsed — so
    early in the month history dominates, and by the end it doesn't count at all.
    """
    remaining = max(total_days - elapsed, 0)
    if elapsed <= 0:
        return historical_month_avg
    if remaining == 0:
        return spent_to_date

    weight = elapsed / total_days
    daily = weight * (spent_to_date / elapsed) + (1 - weight) * (historical_month_avg / total_days)
    return int(spent_to_date + daily * remaining)


def days_in_month(key: str) -> int:
    start, end = month_bounds(key)
    return end.day
