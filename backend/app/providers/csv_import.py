"""Statement-file importer — the route for banks without a live feed.

Recognises Starling and Amex exports from their headers. Anything else (HSBC,
Chase, ...) is read with a generic mapping: a date column, a description, and
either one signed amount or separate paid-in / paid-out columns. Files with no
header row work too, as long as there is exactly one money column to read.

Sign convention: negative = money out. Amex exports purchases as positive
numbers and are flipped automatically. For other card statements the import
endpoint flips when the target account is a credit card and most rows are
positive, and the "Flip signs" box overrides everything.

Statement rows carry no stable id, so one is made from the row's date, size and
description, plus its position among identical rows that day. That makes
re-importing an overlapping file safe (duplicates are skipped), and because the
sign is left out, re-importing with the signs corrected fixes the rows in place
instead of doubling them.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import re
from collections import Counter
from dataclasses import dataclass

from ..money import to_minor
from .base import ProviderError, RawTransaction

_DATE_FORMATS = ["%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y", "%d %b %Y", "%d %B %Y", "%d-%m-%Y", "%d %b %y", "%m/%d/%Y"]
_MONEY = re.compile(r"^[+-]?£?\s*-?[\d,]*\.?\d+$")

# Headers that only Amex exports have.
_AMEX_ONLY = {"cardmember", "appearsonyourstatementas", "extendeddetails"}
# Every header name the generic mapping understands; a first row containing
# none of them is treated as data, not headers.
_DATE_HEADERS = ("transactiondate", "date", "datetime", "posteddate", "postingdate", "bookingdate")
_DESC_HEADERS = ("transactiondescription", "description", "counterparty", "details", "narrative", "payee", "merchant", "name")
_REF_HEADERS = ("reference", "extendeddetails", "notes", "memo")
_AMOUNT_HEADERS = ("amount", "amountgbp", "value", "amount1", "transactionamount")
_OUT_HEADERS = ("debitamount", "paidout", "moneyout", "debit", "withdrawals", "out")
_IN_HEADERS = ("creditamount", "paidin", "moneyin", "credit", "deposits", "in")
_CATEGORY_HEADERS = ("spendingcategory", "category")
_KNOWN = set(_DATE_HEADERS + _DESC_HEADERS + _REF_HEADERS + _AMOUNT_HEADERS + _OUT_HEADERS + _IN_HEADERS + _CATEGORY_HEADERS)


@dataclass
class ParsedStatement:
    rows: list[RawTransaction]
    detected_format: str
    flipped: bool


def _norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (header or "").lower())


def _try_date(value: str) -> dt.date | None:
    value = (value or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _parse_date(value: str) -> dt.date:
    parsed = _try_date(value)
    if parsed is None:
        raise ProviderError(f"Unrecognised date format: {value!r}")
    return parsed


def _money(value: str | None) -> int | None:
    """Pence from a statement amount, or None if it isn't one.
    Handles £, thousands commas, a leading +, and (12.34) for negatives."""
    text = (value or "").strip().replace(" ", "")
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace(",", "").replace("£", "")
    if not _MONEY.match(text):
        return None
    minor = to_minor(text)
    return -abs(minor) if negative else minor


def detect_format(headers: list[str]) -> str:
    normalised = {_norm(h) for h in headers}
    if not normalised & _KNOWN and any(_try_date(h) for h in headers):
        return "headerless"
    if "counterparty" in normalised and any(h.startswith("amount") for h in normalised):
        return "starling"
    if normalised & _AMEX_ONLY and {"date", "amount"} <= normalised:
        return "amex"
    return "generic"


def _decode(content: bytes | str) -> str:
    return content.decode("utf-8-sig", errors="replace") if isinstance(content, bytes) else content


def _rows(text: str) -> list[list[str]]:
    return [row for row in csv.reader(io.StringIO(text)) if any(cell.strip() for cell in row)]


def sniff(content: bytes | str) -> str:
    """Which statement format the file looks like, from its first row."""
    rows = _rows(_decode(content))
    return detect_format(rows[0]) if rows else "generic"


def _find(headers: dict[str, int], candidates: tuple[str, ...]) -> int | None:
    for candidate in candidates:
        if candidate in headers:
            return headers[candidate]
    return None


def _headerless_columns(rows: list[list[str]]) -> tuple[int, int, list[int]]:
    """(date column, amount column, description columns) for a file with no
    header row, worked out from what the cells contain."""
    sample = rows[: min(len(rows), 20)]
    width = max(len(r) for r in sample)

    def share(col: int, test) -> float:
        cells = [r[col] for r in sample if col < len(r) and r[col].strip()]
        return sum(1 for c in cells if test(c)) / len(cells) if cells else 0.0

    date_cols = [c for c in range(width) if share(c, _try_date) >= 0.9]
    money_cols = [c for c in range(width) if c not in date_cols and share(c, lambda v: _money(v) is not None) >= 0.9]
    if not date_cols:
        raise ProviderError("The file has no header row, and no column of dates was found")
    if len(money_cols) != 1:
        raise ProviderError(
            "The file has no header row and "
            + ("no column of amounts" if not money_cols else f"{len(money_cols)} columns of numbers (amount, balance, ...)")
            + ". Add a first line naming the columns, e.g. Date,Description,Amount or "
            "Date,Description,Paid out,Paid in,Balance, and import it again."
        )
    desc_cols = [c for c in range(width) if c not in date_cols and c not in money_cols]
    return date_cols[0], money_cols[0], desc_cols


def parse_statement(content: bytes | str, *, flip_sign: bool = False) -> ParsedStatement:
    rows = _rows(_decode(content))
    if not rows:
        raise ProviderError("The file is empty")

    detected = detect_format(rows[0])
    if detected == "headerless":
        date_i, amount_i, desc_is = _headerless_columns(rows)
        out_i = in_i = ref_i = category_i = None
        data = rows
        desc_i = None
    else:
        header_row, data = rows[0], rows[1:]
        headers = {_norm(h): i for i, h in enumerate(header_row) if _norm(h)}
        date_i = _find(headers, _DATE_HEADERS)
        desc_i = _find(headers, _DESC_HEADERS)
        ref_i = _find(headers, _REF_HEADERS)
        amount_i = _find(headers, _AMOUNT_HEADERS)
        out_i = _find(headers, _OUT_HEADERS)
        in_i = _find(headers, _IN_HEADERS)
        category_i = _find(headers, _CATEGORY_HEADERS)
        desc_is = []
        if date_i is None or (amount_i is None and out_i is None and in_i is None):
            raise ProviderError(
                f"Could not find date and amount columns in: {', '.join(h for h in header_row if h)}"
            )

    # Amex exports purchases as positive numbers.
    flip = flip_sign or detected == "amex"

    def cell(row: list[str], index: int | None) -> str:
        return row[index].strip() if index is not None and index < len(row) else ""

    seen: Counter[str] = Counter()
    out: list[RawTransaction] = []
    for row in data:
        booked_on = _try_date(cell(row, date_i))
        if booked_on is None:
            continue  # blank, a sub-header, or a summary line

        if amount_i is not None:
            minor = _money(cell(row, amount_i)) or 0
        else:
            minor = abs(_money(cell(row, in_i)) or 0) - abs(_money(cell(row, out_i)) or 0)
        if flip:
            minor = -minor
        if minor == 0:
            continue

        if desc_is:
            name = " ".join(cell(row, i) for i in desc_is if cell(row, i))
        else:
            name = cell(row, desc_i)
        reference = cell(row, ref_i)
        description = " ".join(p for p in (name, reference) if p) or "Transaction"

        fingerprint = f"{booked_on.isoformat()}|{abs(minor)}|{description.lower()}"
        seen[fingerprint] += 1
        digest = hashlib.sha1(f"{fingerprint}|{seen[fingerprint]}".encode()).hexdigest()[:24]

        out.append(
            RawTransaction(
                external_id=f"csv:{digest}",
                booked_on=booked_on,
                amount_minor=minor,
                description=description,
                merchant=name or description,
                provider_category=cell(row, category_i) or None,
            )
        )

    if not out:
        raise ProviderError("No usable rows found in the file")
    return ParsedStatement(rows=out, detected_format=detected, flipped=flip)


def parse_csv(content: bytes | str, *, fmt: str | None = None, flip_sign: bool = False) -> list[RawTransaction]:
    """Rows only; kept for callers that don't need the details."""
    return parse_statement(content, flip_sign=flip_sign).rows
