"""Statement-file importer — the fallback when an API route isn't available.

Auto-detects Starling and Amex exports from their headers, and falls back to a
generic date/description/amount (or paid-in/paid-out) mapping. Because statement rows carry
no stable id, we synthesise one from the row's content plus its position among
identical rows on the same day, which keeps re-importing an overlapping file
idempotent.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import re
from collections import Counter

from ..money import to_minor
from .base import ProviderError, RawTransaction

_DATE_FORMATS = ["%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y", "%d %b %Y", "%d %B %Y", "%m/%d/%Y"]


def _norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", header.lower())


def _parse_date(value: str) -> dt.date:
    value = (value or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ProviderError(f"Unrecognised date format: {value!r}")


def _find(headers: dict[str, str], *candidates: str) -> str | None:
    for candidate in candidates:
        if candidate in headers:
            return headers[candidate]
    return None


def detect_format(headers: list[str]) -> str:
    normalised = {_norm(h) for h in headers}
    if "counterparty" in normalised and any(h.startswith("amount") for h in normalised):
        return "starling"
    if {"date", "description", "amount"} <= normalised and "cardmember" in normalised:
        return "amex"
    if {"date", "description", "amount"} <= normalised:
        return "amex_or_generic"
    return "generic"


def sniff(content: bytes | str) -> str:
    """Which statement format the file looks like, from its header row alone."""
    text = _decode(content)
    reader = csv.reader(io.StringIO(text))
    try:
        headers = next(reader)
    except StopIteration:
        return "generic"
    return detect_format(headers)


def _decode(content: bytes | str) -> str:
    return content.decode("utf-8-sig", errors="replace") if isinstance(content, bytes) else content


def parse_csv(content: bytes | str, *, fmt: str | None = None, flip_sign: bool = False) -> list[RawTransaction]:
    text = _decode(content)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ProviderError("The file has no header row")

    headers = {_norm(h): h for h in reader.fieldnames}
    detected = fmt or detect_format(reader.fieldnames)
    # Amex reports a purchase as a positive number; everything else is signed.
    flip = flip_sign or detected in {"amex", "amex_or_generic"}

    date_col = _find(headers, "transactiondate", "date", "datetime", "posteddate")
    desc_col = _find(headers, "transactiondescription", "description", "counterparty", "details", "narrative")
    ref_col = _find(headers, "reference", "extendeddetails", "notes")
    amount_col = _find(headers, "amount", "amountgbp", "value", "amount1")
    debit_col = _find(headers, "debitamount", "paidout", "moneyout")
    credit_col = _find(headers, "creditamount", "paidin", "moneyin")
    category_col = _find(headers, "spendingcategory", "category")

    if not date_col or not (amount_col or debit_col or credit_col):
        raise ProviderError(
            f"Could not find date/amount columns in: {', '.join(reader.fieldnames)}"
        )

    seen: Counter[str] = Counter()
    out: list[RawTransaction] = []
    for row in reader:
        raw_date = (row.get(date_col) or "").strip()
        if not raw_date:
            continue
        booked_on = _parse_date(raw_date)

        if amount_col:
            minor = to_minor((row.get(amount_col) or "0").replace(",", "").replace("£", "").strip())
            if flip:
                minor = -minor
        else:
            debit = to_minor((row.get(debit_col) or "0").replace(",", "").strip()) if debit_col else 0
            credit = to_minor((row.get(credit_col) or "0").replace(",", "").strip()) if credit_col else 0
            minor = credit - debit
        if minor == 0:
            continue

        description = " ".join(
            p for p in ((row.get(desc_col) or "").strip(), (row.get(ref_col) or "").strip() if ref_col else "")
            if p
        ) or "Transaction"

        fingerprint = f"{booked_on.isoformat()}|{minor}|{description.lower()}"
        seen[fingerprint] += 1
        digest = hashlib.sha1(f"{fingerprint}|{seen[fingerprint]}".encode()).hexdigest()[:24]

        out.append(
            RawTransaction(
                external_id=f"csv:{digest}",
                booked_on=booked_on,
                amount_minor=minor,
                description=description,
                merchant=(row.get(desc_col) or description).strip(),
                provider_category=(row.get(category_col) or None) if category_col else None,
            )
        )

    if not out:
        raise ProviderError("No usable rows found in the file")
    return out
