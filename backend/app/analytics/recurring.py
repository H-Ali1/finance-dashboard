"""Recurring-payment detection: subscriptions, direct debits, standing orders.

Nothing here is hard-coded to a list of known services. A payment is treated as
recurring when the same merchant charges you at a regular *cadence* with a
roughly *stable amount* — which catches the gym and the window cleaner as well
as Netflix, and keeps working when a new service launches.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from itertools import pairwise
from statistics import median

from sqlalchemy.orm import Session

from ..money import to_major
from .common import account_map, is_spend, load_transactions, robust_stats

LOOKBACK_DAYS = 420

# (label, low, high, days) — bounds are generous because billing dates drift.
_CADENCES = [
    ("weekly", 6, 8, 7),
    ("fortnightly", 12, 16, 14),
    ("four-weekly", 26, 29, 28),
    ("monthly", 29, 34, 30.44),
    ("two-monthly", 55, 66, 60.9),
    ("quarterly", 84, 96, 91.3),
    ("half-yearly", 175, 190, 182.6),
    ("yearly", 350, 380, 365),
]

# Categories where regular spending is normal shopping, not a subscription.
_NOISY_CATEGORIES = {"Groceries", "Eating Out", "Fuel", "Cash", "Transfers"}

# Rent and the gas bill are recurring, but they aren't things you can cancel on
# a whim — keeping them apart stops "subscriptions" totals reading as absurd.
_BILL_CATEGORIES = {
    "Rent & Mortgage",
    "Bills & Utilities",
    "Insurance",
    "Education",
    "Fees & Interest",
    "Savings & Investments",
}


def _classify_cadence(median_gap: float) -> tuple[str, float] | None:
    for label, low, high, days in _CADENCES:
        if low <= median_gap <= high:
            return label, days
    return None


def service_family(merchant_key: str) -> str:
    """Collapse 'apple com bill' / 'apple services' onto one family for dupe checks."""
    words = [w for w in merchant_key.split() if len(w) > 2]
    return " ".join(words[:2]) if words else merchant_key


def payee(merchant_key: str) -> str:
    """Who a payment went to, for joining names that drift ("example homes",
    "example homes flat b"). Two words at least: one is often only a
    surname ("m khan" and "a khan" are different people)."""
    family = service_family(merchant_key)
    return family if len(family.split()) >= 2 else merchant_key


# Payment types that are recurring by nature: you set them up to repeat.
SCHEDULED_TYPES = {"standing_order", "direct_debit"}


def _is_scheduled(rows: list) -> bool:
    """Set up to repeat: a standing order or direct debit, or rent."""
    latest = rows[-1]
    return latest.payment_type in SCHEDULED_TYPES or latest.category == "Rent & Mortgage"


def _detect_group(rows: list, accounts: dict, today: dt.date) -> dict | None:
    """One merchant's payments, oldest first: a recurring item, or None."""
    # Collapse same-day duplicates; a double charge shouldn't look like a cadence.
    by_day: dict[dt.date, list] = defaultdict(list)
    for txn in rows:
        by_day[txn.booked_on].append(txn)
    days = sorted(by_day)
    amounts = [sum(-t.amount_minor for t in by_day[d]) for d in days]
    # Three payments make a pattern. Two do when they were set up to repeat
    # (a standing order, rent) and came to the same sum: often all a bank
    # that only shares 89 days of history can show.
    scheduled = _is_scheduled(rows)
    if len(days) < 2 or (
        len(days) == 2 and not (scheduled and abs(amounts[0] - amounts[1]) <= max(100, amounts[1] // 50))
    ):
        return None

    gaps = [(b - a).days for a, b in pairwise(days)]
    med_gap = median(gaps)
    if med_gap < 6:
        return None  # several times a week — a shop, not a subscription

    gap_med, gap_mad = robust_stats([float(g) for g in gaps])
    if gap_med and gap_mad / gap_med > 0.3:
        return None  # too irregular to predict

    cadence = _classify_cadence(med_gap)
    if cadence is None:
        return None
    label, cadence_days = cadence

    amt_med, amt_mad = robust_stats([float(a) for a in amounts])
    if not amt_med:
        return None
    stable = (amt_mad / amt_med) <= 0.25 or len(set(amounts)) == 1
    if not stable:
        return None

    sample = by_day[days[-1]][0]
    if sample.category in _NOISY_CATEGORIES and len(set(amounts)) > 1:
        return None

    last_seen = days[-1]
    due_on = last_seen + dt.timedelta(days=round(med_gap))
    overdue_by = (today - due_on).days
    grace = max(6, round(med_gap * 0.4))
    status = "active"
    if overdue_by > grace * 2:
        status = "stopped"
    elif overdue_by > grace:
        status = "overdue"

    recent = amounts[-1]
    baseline = int(median(amounts[:-1])) if len(amounts) > 1 else recent
    price_change = None
    if baseline and abs(recent - baseline) >= max(50, baseline * 0.02):
        price_change = {
            "from": to_major(baseline),
            "to": to_major(recent),
            "pct": round((recent - baseline) / baseline * 100, 1),
            "direction": "up" if recent > baseline else "down",
            # What the rise costs you over a year if it sticks.
            "annual_impact": to_major(int((recent - baseline) * (365 / cadence_days))),
        }

    key = _key(sample)
    account = accounts.get(sample.account_id)
    is_bill = sample.category in _BILL_CATEGORIES or (
        sample.category == "Uncategorised" and sample.payment_type == "standing_order"
    )
    return {
        "merchant": sample.merchant,
        "merchant_key": key,
        # Every name it has gone by: payees change, and so do references.
        "merchant_keys": sorted({_key(t) for t in rows}),
        "family": service_family(key),
        "category": sample.category,
        "type": "bill" if is_bill else "subscription",
        "cadence": label,
        "cadence_days": round(med_gap, 1),
        "amount": to_major(recent),
        "typical_amount": to_major(int(amt_med)),
        "annual_cost": to_major(int(amt_med * (365 / cadence_days))),
        "monthly_cost": to_major(int(amt_med * (30.44 / cadence_days))),
        "occurrences": len(days),
        "first_seen": days[0].isoformat(),
        "last_seen": last_seen.isoformat(),
        "next_expected": due_on.isoformat(),
        "days_until_due": (due_on - today).days,
        "status": status,
        "price_change": price_change,
        "account": account.name if account else "",
        "account_id": sample.account_id,
        "institution": account.institution_name if account else "",
        "total_paid": to_major(sum(amounts)),
    }


def _key(txn) -> str:
    return txn.merchant_key or txn.merchant.lower()


def detect_recurring(db: Session, lookback_days: int = LOOKBACK_DAYS) -> list[dict]:
    start = dt.date.today() - dt.timedelta(days=lookback_days)
    txns = [t for t in load_transactions(db, start=start) if is_spend(t)]
    accounts = account_map(db)
    today = dt.date.today()

    # Grouped per account, not per merchant: the same service billed on two
    # cards is two subscriptions (and worth flagging), not one erratic one.
    groups: dict[tuple[int, str], list] = defaultdict(list)
    for txn in txns:
        if key := _key(txn):
            groups[(txn.account_id, key)].append(txn)

    found: list[tuple[dict, list]] = []
    # What isn't a pattern under one name at one bank may be under several:
    # "EXAMPLE HOMES" and "EXAMPLE HOMES FLAT B", paid from Starling one
    # month and HSBC the next. Those are tried again by payee, across banks.
    unmatched: dict[str, list[list]] = defaultdict(list)
    for (_account_id, key), rows in groups.items():
        rows.sort(key=lambda t: t.booked_on)
        if item := _detect_group(rows, accounts, today):
            found.append((item, rows))
        else:
            unmatched[payee(key)].append(rows)

    for parts in unmatched.values():
        if len(parts) < 2 or not _one_bank_at_a_time(parts):
            continue  # nothing to join it with, or one service on two cards at once
        rows = sorted((t for part in parts for t in part), key=lambda t: t.booked_on)
        if item := _detect_group(rows, accounts, today):
            found.append((item, rows))

    items = _join_handovers(found, accounts, today)
    items.sort(key=lambda r: r["annual_cost"], reverse=True)
    _flag_duplicates(items)
    return items


def _one_bank_at_a_time(parts: list[list]) -> bool:
    """Names can alternate at one bank, but a second bank must take over
    after the first stops: paid from two at once is two payments."""
    days: dict[int, list[dt.date]] = defaultdict(list)
    for part in parts:
        days[part[0].account_id].extend(t.booked_on for t in part)
    spans = sorted((min(d), max(d)) for d in days.values())
    return all(later[0] > earlier[1] for earlier, later in pairwise(spans))


def _join_handovers(found: list[tuple[dict, list]], accounts: dict, today: dt.date) -> list[dict]:
    """One payee found more than once, one after the other — the payee's name
    changed, or you moved the payment to another bank — is one bill. Found
    side by side (the same service on two cards) they stay apart, so the
    duplicate can be flagged."""
    by_family: dict[str, list[tuple[dict, list]]] = defaultdict(list)
    for item, rows in found:
        by_family[payee(item["merchant_key"])].append((item, rows))

    out: list[dict] = []
    for entries in by_family.values():
        entries.sort(key=lambda e: e[0]["first_seen"])
        one_after_another = all(
            later["first_seen"] > earlier["last_seen"] for (earlier, _), (later, _) in pairwise(entries)
        )
        if len(entries) > 1 and one_after_another:
            rows = sorted((t for _, part in entries for t in part), key=lambda t: t.booked_on)
            if joined := _detect_group(rows, accounts, today):
                out.append(joined)
                continue
        out.extend(item for item, _ in entries)
    return out


def _flag_duplicates(items: list[dict]) -> None:
    """Same service billing on two different accounts/cards."""
    by_family: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        if item["status"] != "stopped":
            by_family[item["family"]].append(item)

    for group in by_family.values():
        if len(group) < 2:
            continue
        accounts = {g["account_id"] for g in group}
        if len(accounts) < 2:
            continue
        for item in group:
            item["duplicate_of"] = [g["account"] for g in group if g is not item]


def summary(items: list[dict]) -> dict:
    active = [i for i in items if i["status"] == "active"]
    subs = [i for i in active if i["type"] == "subscription"]
    bills = [i for i in active if i["type"] == "bill"]
    return {
        "count": len(items),
        "active_count": len(active),
        # Every recurring commitment, bills included.
        "monthly_total": round(sum(i["monthly_cost"] for i in active), 2),
        "annual_total": round(sum(i["annual_cost"] for i in active), 2),
        # The cancellable subset — what "subscriptions" means to a person.
        "subscription_count": len(subs),
        "subscription_monthly": round(sum(i["monthly_cost"] for i in subs), 2),
        "subscription_annual": round(sum(i["annual_cost"] for i in subs), 2),
        "bills_monthly": round(sum(i["monthly_cost"] for i in bills), 2),
        "bills_annual": round(sum(i["annual_cost"] for i in bills), 2),
        "price_rises": [i for i in items if (i.get("price_change") or {}).get("direction") == "up"],
        "stopped": [i for i in items if i["status"] == "stopped"],
        "duplicates": [i for i in items if i.get("duplicate_of")],
    }


def recurring_keys(items: list[dict], *, include_stopped: bool = False) -> set[str]:
    """Merchant keys that bill you on a schedule — used to separate fixed
    commitments from discretionary spending in projections and anomalies."""
    statuses = {"active", "overdue", "stopped"} if include_stopped else {"active", "overdue"}
    return {key for i in items if i["status"] in statuses for key in i.get("merchant_keys", [i["merchant_key"]])}


def upcoming(items: list[dict], days: int = 30) -> list[dict]:
    return sorted(
        (i for i in items if i["status"] in {"active", "overdue"} and 0 <= i["days_until_due"] <= days),
        key=lambda i: i["days_until_due"],
    )
