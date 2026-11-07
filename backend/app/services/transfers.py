"""Detect money moving between your own accounts.

Without this, paying the Amex off from a current account looks like a large
purchase *and* a large income, which wrecks every spend total on the dashboard.
A debit on one account is paired with a credit of the same size on another
within a few days.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Transaction

WINDOW_DAYS = 4
# Ignore tiny amounts: coincidental £3 matches are common and harmless either way.
MIN_MINOR = 500


def detect_transfers(db: Session, since: dt.date | None = None) -> int:
    since = since or dt.date.today() - dt.timedelta(days=400)
    rows = list(
        db.scalars(
            select(Transaction).where(Transaction.booked_on >= since).order_by(Transaction.booked_on)
        )
    )

    credits_by_amount: dict[int, list[Transaction]] = defaultdict(list)
    for txn in rows:
        if txn.amount_minor > MIN_MINOR:
            credits_by_amount[txn.amount_minor].append(txn)

    paired = 0
    used: set[int] = set()
    for debit in rows:
        if debit.amount_minor >= -MIN_MINOR or debit.id in used:
            continue
        candidates = credits_by_amount.get(-debit.amount_minor, [])
        for credit in candidates:
            if credit.id in used or credit.account_id == debit.account_id:
                continue
            if abs((credit.booked_on - debit.booked_on).days) > WINDOW_DAYS:
                continue

            group = f"tr_{uuid.uuid4().hex[:12]}"
            for txn in (debit, credit):
                txn.is_transfer = True
                txn.transfer_group = group
                if txn.category_source != "manual":
                    txn.category = "Transfers"
            used.update({debit.id, credit.id})
            paired += 1
            break

    db.commit()
    return paired
