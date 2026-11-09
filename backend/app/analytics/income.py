"""Income by source, month by month: how much, how regular, which way it's going.

Sources are the income categories: Salary (the day job), Tutoring, and
everything else earned or given (interest, benefits, a tax rebate). Refunds
and your own money coming back from another account are not income; they're
totalled separately so it's clear what was left out.
"""

from __future__ import annotations

import datetime as dt
from statistics import mean, median, pstdev

from sqlalchemy.orm import Session

from ..money import to_major
from ..services.categorise import INCOME_CATEGORIES
from .common import account_map, load_transactions, month_bounds, month_key, month_keys_back, pct_change

SOURCES = [("Salary", "Salary"), ("Tutoring", "Tutoring"), ("Income", "Other income")]
# A change smaller than this, either way, is "steady".
STEADY_PCT = 5.0


def _payer_key(txn) -> str:
    return txn.merchant_key or (txn.merchant or "").lower()


def _median_int(values: list[int]) -> int:
    return int(median(values)) if values else 0


def _source(category: str, label: str, months: dict[str, dict], complete: list[str], current: str) -> dict:
    totals = [months[k]["total"] for k in complete]
    paid = [k for k in complete if months[k]["total"] > 0]
    recent, previous = complete[-3:], complete[-6:-3]
    recent_avg = mean(months[k]["total"] for k in recent) if recent else 0
    previous_avg = mean(months[k]["total"] for k in previous) if previous else 0
    change = pct_change(recent_avg, previous_avg) if previous and previous_avg else None
    direction = None
    if change is not None:
        direction = "up" if change >= STEADY_PCT else "down" if change <= -STEADY_PCT else "steady"

    paid_totals = [months[k]["total"] for k in paid]
    variation = (
        round(pstdev(paid_totals) / mean(paid_totals) * 100, 1) if len(paid_totals) >= 2 and mean(paid_totals) else None
    )
    days = [day for k in paid for day in months[k]["days"]]
    if not complete:
        regularity = "Not enough history yet"
    elif not paid:
        regularity = f"None in the last {len(complete)} months"
    elif len(paid) == len(complete):
        regularity = "Every month"
    else:
        regularity = f"{len(paid)} of the last {len(complete)} months"

    return {
        "source": category,
        "label": label,
        "this_month": to_major(months[current]["total"]) if current in months else 0.0,
        "this_month_payments": months[current]["payments"] if current in months else 0,
        # What a normal month brings: the median, so one big month doesn't set it.
        "typical": to_major(_median_int(totals)),
        "average": to_major(int(mean(totals))) if totals else 0.0,
        "total": to_major(sum(m["total"] for m in months.values())),
        "months_paid": len(paid),
        "months_counted": len(complete),
        "regularity": regularity,
        "variation_pct": variation,
        "payday": _median_int([d.day for d in days]) or None,
        "payments_per_month": _median_int([months[k]["payments"] for k in paid]),
        "payers_per_month": _median_int([len(months[k]["payers"]) for k in paid]),
        "payers": len({p for m in months.values() for p in m["payers"]}),
        "trend": {
            "recent": to_major(int(recent_avg)),
            "previous": to_major(int(previous_avg)),
            "change_pct": change,
            "direction": direction,
        },
        "series": [
            {"month": k, "total": to_major(m["total"]), "payments": m["payments"], "payers": len(m["payers"])}
            for k, m in months.items()
        ],
    }


def income_breakdown(db: Session, months: int = 12) -> dict:
    keys = month_keys_back(max(months, 1))
    start, _ = month_bounds(keys[0])
    today = dt.date.today()
    current = month_key(today)
    complete = [k for k in keys if k != current]
    accounts = account_map(db)

    by_source = {
        category: {k: {"total": 0, "payments": 0, "payers": set(), "days": []} for k in keys} for category, _ in SOURCES
    }
    refunds = dict.fromkeys(keys, 0)
    own_money = dict.fromkeys(keys, 0)
    payers: dict[str, dict] = {}

    for txn in sorted(load_transactions(db, start=start, include_transfers=True), key=lambda t: t.booked_on):
        key = month_key(txn.booked_on)
        if txn.amount_minor <= 0 or key not in refunds:
            continue
        if txn.is_transfer or txn.category in ("Transfers", "Savings & Investments"):
            own_money[key] += txn.amount_minor
            continue
        if txn.category not in INCOME_CATEGORIES:
            refunds[key] += txn.amount_minor
            continue

        payer = _payer_key(txn)
        month = by_source[txn.category][key]
        month["total"] += txn.amount_minor
        month["payments"] += 1
        month["payers"].add(payer)
        month["days"].append(txn.booked_on)

        account = accounts.get(txn.account_id)
        row = payers.setdefault(
            payer, {"merchant_key": payer, "payments": 0, "total_minor": 0, "months": set(), "accounts": set()}
        )
        row.update(  # sorted oldest first, so the latest payment has the last word
            payer=txn.merchant,
            source=txn.category,
            source_set_by=txn.category_source,
            last_date=txn.booked_on.isoformat(),
            last_amount=to_major(txn.amount_minor),
        )
        row["payments"] += 1
        row["total_minor"] += txn.amount_minor
        row["months"].add(key)
        if account:
            row["accounts"].add(account.name)

    sources = [_source(category, label, by_source[category], complete, current) for category, label in SOURCES]
    monthly_totals = [sum(by_source[c][k]["total"] for c, _ in SOURCES) for k in complete]
    ranked = sorted(payers.values(), key=lambda p: p["total_minor"], reverse=True)
    return {
        "months": keys,
        "current_month": current,
        "sources": sources,
        "total": {
            "this_month": to_major(sum(by_source[c][current]["total"] for c, _ in SOURCES if current in by_source[c])),
            "typical": to_major(_median_int(monthly_totals)),
        },
        "series": [{"month": k, **{c: to_major(by_source[c][k]["total"]) for c, _ in SOURCES}} for k in keys],
        # Left out of income on purpose, so you can see it was.
        "not_income": {
            "refunds_this_month": to_major(refunds.get(current, 0)),
            "own_money_this_month": to_major(own_money.get(current, 0)),
            "refunds": to_major(sum(refunds.values())),
            "own_money": to_major(sum(own_money.values())),
        },
        "payers": [
            {
                **{k: v for k, v in p.items() if k not in ("total_minor", "months", "accounts")},
                "total": to_major(p["total_minor"]),
                "months": len(p["months"]),
                "accounts": sorted(p["accounts"]),
            }
            for p in ranked[:40]
        ],
    }
