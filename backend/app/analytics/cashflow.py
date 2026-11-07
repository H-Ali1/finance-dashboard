"""Income vs outgoings, month-end projection, savings rate and cash runway."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from statistics import median

from sqlalchemy.orm import Session

from ..config import settings
from ..money import to_major
from .common import (
    days_in_month,
    is_income,
    is_spend,
    load_transactions,
    month_bounds,
    month_key,
    month_keys_back,
    project_month_total,
)
from .networth import compute_networth
from .recurring import detect_recurring, recurring_keys, upcoming


def _month_totals(db: Session, months: int, fixed_keys: set[str]) -> dict[str, dict[str, int]]:
    keys = month_keys_back(months)
    start, _ = month_bounds(keys[0])
    txns = load_transactions(db, start=start)
    totals = {
        k: {"income": 0, "spend": 0, "essential": 0, "discretionary": 0, "fixed": 0, "variable": 0}
        for k in keys
    }
    essentials = set(settings.essential_category_list)

    for txn in txns:
        key = month_key(txn.booked_on)
        if key not in totals:
            continue
        if is_spend(txn):
            amount = -txn.amount_minor
            totals[key]["spend"] += amount
            totals[key]["essential" if txn.category in essentials else "discretionary"] += amount
            is_fixed = (txn.merchant_key or txn.merchant.lower()) in fixed_keys
            totals[key]["fixed" if is_fixed else "variable"] += amount
        elif is_income(txn):
            totals[key]["income"] += txn.amount_minor
    return totals


def _mean(values: list[int]) -> int:
    return int(sum(values) / len(values)) if values else 0


def _median(values: list[int]) -> int:
    return int(median(values)) if values else 0


def cashflow(db: Session, months: int = 12) -> dict:
    recurring_items = detect_recurring(db)
    fixed_keys = recurring_keys(recurring_items)
    totals = _month_totals(db, months, fixed_keys)
    keys = list(totals)
    current_key = keys[-1]
    history_keys = keys[:-1]

    today = dt.date.today()
    total_days = days_in_month(current_key)
    elapsed = min(today.day, total_days) if month_key(today) == current_key else total_days
    remaining_days = max(total_days - elapsed, 0)

    current = totals[current_key]
    # A settled month is one we have complete data for; averages ignore the part-month.
    complete = [totals[k] for k in history_keys] or [current]
    avg_income = _mean([m["income"] for m in complete])
    avg_spend = _mean([m["spend"] for m in complete])
    # A holiday or a new laptop drags the mean around for months. The median is
    # what a normal month actually looks like, so projections and the runway
    # calculation use it; the mean is still reported for reference.
    typical_income = _median([m["income"] for m in complete])
    typical_spend = _median([m["spend"] for m in complete])
    avg_essential = _median([m["essential"] for m in complete])
    avg_variable = _median([m["variable"] for m in complete])

    # Fixed commitments are projected from what's actually scheduled, not from a
    # daily rate — rent landing on the 1st shouldn't imply a rent every day.
    due = upcoming(recurring_items, days=remaining_days or 30)
    committed_minor = int(round(sum(i["amount"] for i in due) * 100))
    projected_variable = project_month_total(
        current["variable"], elapsed, total_days, avg_variable
    )
    projected_spend = current["fixed"] + committed_minor + projected_variable
    daily_spend = current["spend"] / elapsed if elapsed else 0

    # Income this month is usually one salary — assume the rest of it has landed
    # if the typical payday has passed, otherwise expect the historical figure.
    expected_income = max(
        current["income"], typical_income if remaining_days > 0 else current["income"]
    )

    nw = compute_networth(db)
    liquid_minor = nw["_minor"]["liquid"]
    runway_months = round(liquid_minor / avg_essential, 1) if avg_essential else None

    projected_net = expected_income - projected_spend

    return {
        "month": current_key,
        "days_elapsed": elapsed,
        "days_in_month": total_days,
        "income_so_far": to_major(current["income"]),
        "spend_so_far": to_major(current["spend"]),
        "net_so_far": to_major(current["income"] - current["spend"]),
        "daily_spend_rate": to_major(int(daily_spend)),
        "fixed_so_far": to_major(current["fixed"]),
        "variable_so_far": to_major(current["variable"]),
        "projected_variable": to_major(projected_variable),
        "projected_spend": to_major(projected_spend),
        "projected_income": to_major(expected_income),
        "projected_net": to_major(projected_net),
        "committed_upcoming": to_major(committed_minor),
        "upcoming": due[:12],
        "average_income": to_major(avg_income),
        "average_spend": to_major(avg_spend),
        "typical_income": to_major(typical_income),
        "typical_spend": to_major(typical_spend),
        "typical_essential": to_major(avg_essential),
        "typical_discretionary": to_major(typical_spend - avg_essential),
        "typical_variable": to_major(avg_variable),
        "typical_fixed": to_major(typical_spend - avg_variable),
        "months_of_history": len(complete),
        "savings_rate": round((typical_income - typical_spend) / typical_income * 100, 1)
        if typical_income
        else None,
        "current_savings_rate": round(projected_net / expected_income * 100, 1)
        if expected_income
        else None,
        "liquid": to_major(liquid_minor),
        "runway_months": runway_months,
        "history": [
            {
                "month": k,
                "income": to_major(v["income"]),
                "spend": to_major(v["spend"]),
                "essential": to_major(v["essential"]),
                "discretionary": to_major(v["discretionary"]),
                "fixed": to_major(v["fixed"]),
                "variable": to_major(v["variable"]),
                "net": to_major(v["income"] - v["spend"]),
            }
            for k, v in totals.items()
        ],
        "_minor": {
            "projected_net": projected_net,
            "typical_income": typical_income,
            "typical_spend": typical_spend,
            "typical_essential": avg_essential,
            "liquid": liquid_minor,
        },
    }


def daily_burn(db: Session, days: int = 60) -> dict:
    """Cumulative spend by day — the shape of the month, not just the total."""
    start = dt.date.today() - dt.timedelta(days=days)
    txns = load_transactions(db, start=start)
    per_day: dict[str, int] = defaultdict(int)
    for txn in txns:
        if is_spend(txn):
            per_day[txn.booked_on.isoformat()] += -txn.amount_minor

    series = []
    running = 0
    day = start
    while day <= dt.date.today():
        amount = per_day.get(day.isoformat(), 0)
        running += amount
        series.append({"date": day.isoformat(), "spend": to_major(amount), "cumulative": to_major(running)})
        day += dt.timedelta(days=1)
    return {"series": series}
