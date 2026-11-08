"""Category breakdowns, month-on-month trends and budget tracking."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Budget, Transaction
from ..money import to_major
from .common import (
    account_map,
    days_in_month,
    is_income,
    is_spend,
    load_transactions,
    month_bounds,
    month_key,
    month_keys_back,
    pct_change,
    project_month_total,
)


def category_breakdown(db: Session, start: dt.date, end: dt.date) -> dict:
    txns = load_transactions(db, start=start, end=end)
    totals: dict[str, int] = defaultdict(int)
    counts: dict[str, int] = defaultdict(int)
    for txn in txns:
        if is_spend(txn):
            totals[txn.category] += -txn.amount_minor
            counts[txn.category] += 1

    grand = sum(totals.values())
    rows = [
        {
            "category": category,
            "total": to_major(amount),
            "total_minor": amount,
            "transactions": counts[category],
            "share": round(amount / grand * 100, 1) if grand else 0.0,
        }
        for category, amount in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    ]
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "total_spend": to_major(grand),
        "categories": rows,
    }


def monthly_trend(db: Session, months: int = 12) -> dict:
    keys = month_keys_back(months)
    start, _ = month_bounds(keys[0])
    txns = load_transactions(db, start=start)

    buckets: dict[str, dict[str, int]] = {k: {"income": 0, "spend": 0} for k in keys}
    for txn in txns:
        key = month_key(txn.booked_on)
        if key not in buckets:
            continue
        if is_spend(txn):
            buckets[key]["spend"] += -txn.amount_minor
        elif is_income(txn):
            buckets[key]["income"] += txn.amount_minor

    series = []
    for key in keys:
        income = buckets[key]["income"]
        spend = buckets[key]["spend"]
        series.append(
            {
                "month": key,
                "income": to_major(income),
                "spend": to_major(spend),
                "net": to_major(income - spend),
                "savings_rate": round((income - spend) / income * 100, 1) if income else None,
            }
        )
    return {"months": series}


def category_trend(db: Session, months: int = 6, limit: int = 8) -> dict:
    """Per-category spend by month, plus how the latest month compares.

    On the 3rd of the month, comparing three days of spending against whole
    previous months would say every category has collapsed. The comparison is
    therefore like-for-like: month-to-date now against the same stretch of each
    previous month. `series` stays as full months, for the chart.
    """
    keys = month_keys_back(months)
    start, _ = month_bounds(keys[0])
    txns = load_transactions(db, start=start)

    today = dt.date.today()
    current, *previous = list(reversed(keys))
    partial = month_key(today) == current
    cutoff_day = today.day if partial else 31

    full: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(keys, 0))
    to_date: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(keys, 0))
    for txn in txns:
        key = month_key(txn.booked_on)
        if key not in full[txn.category] or not is_spend(txn):
            continue
        full[txn.category][key] += -txn.amount_minor
        if txn.booked_on.day <= cutoff_day:
            to_date[txn.category][key] += -txn.amount_minor

    rows = []
    for category, by_month in full.items():
        now = to_date[category][current]
        history = [to_date[category][k] for k in previous]
        average = sum(history) / len(history) if history else 0
        if now == 0 and average == 0:
            continue  # nothing to compare — don't pad the table with zeroes
        rows.append(
            {
                "category": category,
                "current": to_major(now),
                "average": to_major(int(average)),
                "change_pct": pct_change(now, average),
                "change": to_major(int(now - average)),
                "series": [{"month": k, "total": to_major(by_month[k])} for k in keys],
            }
        )

    rows.sort(key=lambda r: r["current"], reverse=True)
    return {
        "months": keys,
        "current_month": current,
        "comparison": "month-to-date" if partial else "full-month",
        "through_day": cutoff_day if partial else None,
        "categories": rows[:limit],
    }


def top_merchants(db: Session, start: dt.date, end: dt.date, limit: int = 15) -> dict:
    txns = load_transactions(db, start=start, end=end)
    totals: dict[str, int] = defaultdict(int)
    counts: dict[str, int] = defaultdict(int)
    labels: dict[str, str] = {}
    categories: dict[str, str] = {}
    for txn in txns:
        if not is_spend(txn):
            continue
        key = txn.merchant_key or txn.merchant.lower()
        totals[key] += -txn.amount_minor
        counts[key] += 1
        labels.setdefault(key, txn.merchant)
        categories.setdefault(key, txn.category)

    rows = [
        {
            "merchant": labels[key],
            "merchant_key": key,
            "category": categories[key],
            "total": to_major(amount),
            "transactions": counts[key],
            "average": to_major(int(amount / counts[key])),
        }
        for key, amount in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    ]
    return {"merchants": rows}


def budget_status(db: Session, key: str | None = None) -> dict:
    key = key or month_key(dt.date.today())
    start, end = month_bounds(key)
    today = dt.date.today()
    txns = load_transactions(db, start=start, end=end)

    spent: dict[str, int] = defaultdict(int)
    for txn in txns:
        if is_spend(txn):
            spent[txn.category] += -txn.amount_minor

    total_days = days_in_month(key)
    elapsed = min(today.day, total_days) if month_key(today) == key else total_days

    # What this category normally costs in a full month, for the projection.
    # Median across recent months, so one holiday doesn't redefine "normal".
    history_keys = [k for k in month_keys_back(7) if k != key]
    history_start, _ = month_bounds(history_keys[0]) if history_keys else (start, end)
    history: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(history_keys, 0))
    for txn in load_transactions(db, start=history_start, end=start - dt.timedelta(days=1)):
        month = month_key(txn.booked_on)
        if is_spend(txn) and month in history_keys:
            history[txn.category][month] += -txn.amount_minor

    rows = []
    for budget in db.scalars(select(Budget).order_by(Budget.category)):
        used = spent.get(budget.category, 0)
        past = list(history[budget.category].values()) if budget.category in history else []
        typical = int(median(past)) if past else 0
        projected = project_month_total(used, elapsed, total_days, typical)
        rows.append(
            {
                "category": budget.category,
                "limit": to_major(budget.monthly_limit_minor),
                "spent": to_major(used),
                "remaining": to_major(budget.monthly_limit_minor - used),
                "used_pct": round(used / budget.monthly_limit_minor * 100, 1) if budget.monthly_limit_minor else None,
                "typical": to_major(typical),
                "projected": to_major(projected),
                "on_track": projected <= budget.monthly_limit_minor,
            }
        )

    budgeted = {r["category"] for r in rows}
    unbudgeted = [
        {"category": category, "spent": to_major(amount)}
        for category, amount in sorted(spent.items(), key=lambda kv: kv[1], reverse=True)
        if category not in budgeted
    ]
    return {
        "month": key,
        "days_elapsed": elapsed,
        "days_in_month": total_days,
        "budgets": rows,
        "unbudgeted": unbudgeted,
        "total_spent": to_major(sum(spent.values())),
    }


def account_spend(db: Session, start: dt.date, end: dt.date) -> dict:
    txns = load_transactions(db, start=start, end=end)
    accounts = account_map(db)
    totals: dict[int, int] = defaultdict(int)
    for txn in txns:
        if is_spend(txn):
            totals[txn.account_id] += -txn.amount_minor
    rows = [
        {
            "account": accounts[aid].name if aid in accounts else f"Account {aid}",
            "institution": accounts[aid].institution_name if aid in accounts else "",
            "total": to_major(amount),
        }
        for aid, amount in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    ]
    return {"accounts": rows}


def uncategorised_count(db: Session) -> int:
    return len([t for t in db.scalars(select(Transaction).where(Transaction.category == "Uncategorised"))])
