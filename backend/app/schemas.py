"""Request/response models for the write endpoints."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field


class AccountUpdate(BaseModel):
    name: str | None = None
    kind: str | None = Field(default=None, description="current|savings|credit_card|investment|loan|mortgage")
    include_in_networth: bool | None = None
    is_active: bool | None = None
    flip_sign: bool | None = Field(
        default=None,
        description="Set when a provider reports spend as positive (some card issuers do).",
    )
    income_default: str | None = Field(
        default=None,
        description='"Tutoring": bank transfers from people into this account are tutoring. "" turns it off.',
    )


class TransactionUpdate(BaseModel):
    category: str | None = None
    merchant: str | None = None
    notes: str | None = None
    excluded: bool | None = None
    is_transfer: bool | None = None


class RuleCreate(BaseModel):
    """A rule: every condition given must hold, then every action applies.
    At least one of each."""

    # --- conditions
    pattern: str = Field(default="", max_length=160)
    match_type: Literal["contains", "words", "exact", "regex"] = "contains"
    field: Literal["merchant", "description"] = "merchant"
    direction: Literal["in", "out"] | None = None
    # The size of the amount in pounds, whichever way the money went.
    amount_min: float | None = Field(default=None, ge=0)
    amount_max: float | None = Field(default=None, ge=0)
    account_id: int | None = None
    payment_type: str | None = None
    # --- actions
    category: str | None = Field(default=None, max_length=48)
    rename_to: str | None = Field(default=None, max_length=160)
    exclude: bool = False

    priority: int = 100
    apply_now: bool = True


class RulePreview(RuleCreate):
    # The rule being changed, when editing one.
    rule_id: int | None = None


class MerchantCategory(BaseModel):
    """Categorise everything from one merchant, past and future."""

    merchant: str = Field(min_length=1, max_length=160)
    category: str = Field(min_length=1, max_length=48)
    # The stored key, as the list gave it: used as is, not worked out again.
    merchant_key: str | None = Field(default=None, max_length=160)


class BudgetUpsert(BaseModel):
    category: str
    monthly_limit: float


class ManualAssetUpsert(BaseModel):
    name: str
    kind: str = "asset"  # asset|liability
    category: str = "other"  # car|pension|property|other
    value: float
    currency: str = "GBP"
    annual_change_pct: float | None = None
    notes: str | None = None


class DismissRequest(BaseModel):
    key: str


class DateRange(BaseModel):
    start: dt.date
    end: dt.date
