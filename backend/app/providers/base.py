"""Shared shapes every provider normalises into."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


class ProviderError(RuntimeError):
    """Raised when a provider is unreachable, unauthorised or misconfigured."""


@dataclass(slots=True)
class RawAccount:
    external_id: str
    name: str
    kind: str = "current"  # current|savings|credit_card|investment
    currency: str = "GBP"
    balance_minor: int = 0
    available_minor: int | None = None
    institution_name: str = ""
    meta: dict = field(default_factory=dict)


@dataclass(slots=True)
class RawTransaction:
    external_id: str
    booked_on: dt.date
    amount_minor: int  # signed: negative = money out
    description: str
    currency: str = "GBP"
    booked_at: dt.datetime | None = None
    merchant: str = ""
    provider_category: str | None = None
    mcc: str | None = None
    status: str = "booked"  # booked|pending|reversed (cancelled: never stored)
    payment_type: str | None = None  # see categorise.PAYMENT_TYPES
