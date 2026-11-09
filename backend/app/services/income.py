"""A second look at money coming in, once every transaction has a category.

One payment on its own rarely says what it is: "ACME SOFTWARE" could be a
refund or a salary, "M KHAN" a student or a friend paying you back. Seen
together they do:
  - your own name: a payer whose name is on transfers between your
    accounts is you, moving money from an account the app can't see;
  - salary: an organisation paying you every month (or four weeks, or
    fortnight), at roughly the same amount;
  - tutoring: people (not organisations) paying by bank transfer into the
    account you've said your students pay into.
Only automatic categories change; your rules and manual choices are final.
"""

from __future__ import annotations

import re
from collections import defaultdict
from itertools import pairwise
from statistics import median

from ..models import Account, Transaction

# Set on an account (provider_meta) to say who pays into it by bank transfer.
INCOME_DEFAULT_KEY = "income_default"
INCOME_DEFAULTS = {"Tutoring"}

_ORGANISATION = re.compile(
    r"\b(ltd|limited|plc|llp|llc|inc|group|holdings|services|solutions|consulting|systems|technologies|"
    r"council|nhs|trust|university|college|school|academy|bank|hmrc|dwp|payroll|salary|salaries|wages|"
    r"corporation|company|partners|agency|foundation|charity|insurance|pension)\b",
    re.IGNORECASE,
)
SALARY_MIN_MINOR = 30_000  # £300: smaller regular payments are something else
# Accepted gaps between pay days: weekly, fortnightly, four-weekly, monthly.
_PAY_GAPS = ((6, 8), (12, 16), (26, 35))


def looks_like_organisation(text: str) -> bool:
    return bool(_ORGANISATION.search(text or ""))


def _payer(txn: Transaction) -> str:
    return txn.merchant_key or (txn.merchant or "").lower()


def own_names(rows: list[Transaction]) -> set[str]:
    """Names on two or more transfers the app matched between your accounts:
    yours. Your name paying in from an account that isn't connected is then
    you, not income."""
    seen: dict[str, int] = defaultdict(int)
    for txn in rows:
        if txn.is_transfer and (txn.transfer_group or "").startswith("tr_") and (key := _payer(txn)):
            seen[key] += 1
    return {key for key, count in seen.items() if count >= 2}


def _paid_like_a_salary(payments: list[Transaction]) -> bool:
    if len(payments) < 2:
        return False
    days = sorted({t.booked_on for t in payments})
    if len(days) < 2:
        return False
    gap = median((b - a).days for a, b in pairwise(days))
    if not any(low <= gap <= high for low, high in _PAY_GAPS):
        return False
    amounts = [t.amount_minor for t in payments]
    typical = median(amounts)
    if typical < SALARY_MIN_MINOR:
        return False
    # Overtime and bonuses move pay about; a few pounds one month and
    # thousands the next is something else.
    return median(abs(a - typical) for a in amounts) <= typical * 0.25


def refine(rows: list[Transaction], proposals: dict[int, tuple[str, str]], accounts: dict[int, Account]) -> None:
    """Adjust `proposals` (transaction id -> (category, source)) for money in."""
    mine = own_names(rows)

    unsure: dict[str, list[Transaction]] = defaultdict(list)
    for txn in rows:
        category, source = proposals.get(txn.id, (None, None))
        if txn.amount_minor > 0 and category == "Income" and source == "auto":
            unsure[_payer(txn)].append(txn)

    for payer, payments in unsure.items():
        if payer and payer in mine:
            for txn in payments:
                proposals[txn.id] = ("Transfers", "auto")
            continue

        organisation = any(looks_like_organisation(f"{t.description} {t.merchant}") for t in payments)
        if organisation and _paid_like_a_salary(payments):
            for txn in payments:
                proposals[txn.id] = ("Salary", "auto")
            continue

        if organisation:
            continue
        for txn in payments:
            account = accounts.get(txn.account_id)
            default = (account.provider_meta or {}).get(INCOME_DEFAULT_KEY) if account else None
            if default in INCOME_DEFAULTS and txn.payment_type in (None, "transfer"):
                proposals[txn.id] = (default, "auto")
