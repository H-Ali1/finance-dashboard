"""Detect money moving between your own accounts.

Without this, paying the Amex off from a current account looks like a large
purchase *and* a large income, which wrecks every spend total on the dashboard.
A debit on one account is paired with a credit of the same size on another.

Matching on the amount alone would also pair a student's £30 for a lesson
with any £30 you paid someone that week, and hide both. So:
  - a card payment is never one half of a transfer (it's a purchase or a
    refund);
  - between two current accounts the money has to arrive the same day or
    the next (Faster Payments are instant); a few days are allowed only
    where it's clearly your own money moving: to or from a card, savings,
    an investment account or a loan, between pots at one bank, or a
    description that says "transfer".
Pairs are worked out afresh each time, so ones an older, looser rule made
are undone. Telling the app a transaction is or isn't a transfer sticks.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Account, Transaction
from .categorise import categorise

WINDOW_DAYS = 4
SAME_DAY_WINDOW = 1
# Ignore tiny amounts: coincidental £3 matches are common and harmless either way.
MIN_MINOR = 500
# Accounts where money only arrives from (or goes to) your own accounts.
OWN_MONEY_KINDS = {"credit_card", "savings", "investment", "loan", "mortgage"}
_TRANSFER_WORDS = re.compile(r"\b(transfer|trf|tfr|own account|from savings|to savings)\b", re.IGNORECASE)
AUTO_PREFIX = "tr_"
# Stored when you say a transaction is not a transfer, so it's never paired again.
NOT_A_TRANSFER = "user:not"


def _clearly_own_money(txn: Transaction, accounts: dict[int, Account]) -> bool:
    account = accounts.get(txn.account_id)
    return (
        (account is not None and account.kind in OWN_MONEY_KINDS)
        or txn.payment_type == "internal"
        or bool(_TRANSFER_WORDS.search(txn.description or ""))
    )


def _can_pair(txn: Transaction) -> bool:
    if txn.payment_type == "card" or txn.transfer_group == NOT_A_TRANSFER:
        return False
    # Marked as a transfer by you: already out of the totals, leave it be.
    return not (txn.is_transfer and not (txn.transfer_group or "").startswith(AUTO_PREFIX))


def detect_transfers(db: Session, since: dt.date | None = None) -> int:
    since = since or dt.date.today() - dt.timedelta(days=400)
    rows = list(db.scalars(select(Transaction).where(Transaction.booked_on >= since).order_by(Transaction.booked_on)))
    accounts = {a.id: a for a in db.scalars(select(Account))}

    # Start from scratch for the pairs the app made itself.
    previously_paired = [t for t in rows if (t.transfer_group or "").startswith(AUTO_PREFIX)]
    for txn in previously_paired:
        txn.is_transfer = False
        txn.transfer_group = None

    credits_by_amount: dict[int, list[Transaction]] = defaultdict(list)
    for txn in rows:
        if txn.amount_minor > MIN_MINOR and _can_pair(txn):
            credits_by_amount[txn.amount_minor].append(txn)

    paired = 0
    used: set[int] = set()
    for debit in rows:
        if debit.amount_minor >= -MIN_MINOR or debit.id in used or not _can_pair(debit):
            continue
        for credit in credits_by_amount.get(-debit.amount_minor, []):
            if credit.id in used or credit.account_id == debit.account_id:
                continue
            own = _clearly_own_money(debit, accounts) or _clearly_own_money(credit, accounts)
            if abs((credit.booked_on - debit.booked_on).days) > (WINDOW_DAYS if own else SAME_DAY_WINDOW):
                continue

            group = f"{AUTO_PREFIX}{uuid.uuid4().hex[:12]}"
            for txn in (debit, credit):
                txn.is_transfer = True
                txn.transfer_group = group
                if txn.category_source != "manual":
                    txn.category = "Transfers"
            used.update({debit.id, credit.id})
            paired += 1
            break

    # Pairs that no longer hold go back to the category they'd otherwise have.
    unpaired = [t for t in previously_paired if not t.is_transfer and t.category_source != "manual"]
    if unpaired:
        from .sync import _rules  # sync imports this module

        rules = _rules(db)
        for txn in unpaired:
            txn.category, txn.category_source = categorise(
                merchant=txn.merchant,
                description=txn.description,
                amount_minor=txn.amount_minor,
                mcc=txn.mcc,
                provider_category=txn.provider_category,
                payment_type=txn.payment_type,
                rules=rules,
            )

    db.commit()
    return paired
