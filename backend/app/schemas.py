"""Request/response models for the write endpoints."""

from __future__ import annotations

import datetime as dt

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


class TransactionUpdate(BaseModel):
    category: str | None = None
    merchant: str | None = None
    notes: str | None = None
    excluded: bool | None = None
    is_transfer: bool | None = None


class RuleCreate(BaseModel):
    pattern: str
    category: str
    match_type: str = "contains"
    field: str = "merchant"
    priority: int = 100
    apply_now: bool = True


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
