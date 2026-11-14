"""SQLAlchemy models.

Amount convention: `amount_minor` is signed pence from the account holder's
point of view — negative is money leaving, positive is money arriving. This
holds for credit cards too (a purchase on Amex is negative), so spend analysis
never has to special-case an account type.
"""

from __future__ import annotations

import datetime as dt
import json

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# --- account kinds -----------------------------------------------------------
ASSET_KINDS = {"current", "savings", "investment", "asset"}
LIABILITY_KINDS = {"credit_card", "loan", "mortgage", "liability"}


class Connection(Base):
    """One configured link to a provider (Starling token, Trading 212 key, ...).

    Starling and Trading 212 have one each, with their keys in the settings.
    TrueLayer has one per bank, and keeps that bank's refresh token here.
    """

    __tablename__ = "connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))  # starling|trading212|truelayer
    institution_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending|linked|error|expired
    last_synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # TrueLayer: the refresh token for this bank. Never sent to the browser.
    secret: Mapped[str | None] = mapped_column(Text)
    # TrueLayer: when the bank's 90 days of access end.
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # TrueLayer: which bank (provider_id), granted scopes, last attempt.
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    accounts: Mapped[list[Account]] = relationship(back_populates="connection")


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("provider", "external_id", name="uq_account_provider_ext"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int | None] = mapped_column(ForeignKey("connections.id"))
    provider: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(128))
    institution_name: Mapped[str] = mapped_column(String(128), default="")
    kind: Mapped[str] = mapped_column(String(32), default="current")
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    balance_minor: Mapped[int] = mapped_column(Integer, default=0)
    available_minor: Mapped[int | None] = mapped_column(Integer)
    # Starling needs the default category uid to read the transaction feed.
    provider_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    balance_updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    include_in_networth: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    connection: Mapped[Connection | None] = relationship(back_populates="accounts")
    transactions: Mapped[list[Transaction]] = relationship(back_populates="account", cascade="all, delete-orphan")

    @property
    def is_liability(self) -> bool:
        return self.kind in LIABILITY_KINDS

    @property
    def networth_minor(self) -> int:
        """Signed contribution to net worth.

        Credit-card balances arrive as a negative number from most providers but
        some report the debt as positive; we normalise to "always a debt".
        """
        if self.is_liability:
            return -abs(self.balance_minor)
        return self.balance_minor


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        # One row per bank transaction: the same transaction can't be stored twice.
        UniqueConstraint("account_id", "external_id", name="uq_txn_account_ext"),
        # "Newest transaction in this account", read before every sync.
        Index("ix_transactions_account_booked", "account_id", "booked_on"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(160))
    booked_on: Mapped[dt.date] = mapped_column(Date, index=True)
    booked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    description: Mapped[str] = mapped_column(Text, default="")
    merchant: Mapped[str] = mapped_column(String(160), default="", index=True)
    merchant_key: Mapped[str] = mapped_column(String(160), default="", index=True)
    category: Mapped[str] = mapped_column(String(48), default="Uncategorised", index=True)
    # auto|rule|manual|learned ("learned": guessed from the merchants you've categorised)
    category_source: Mapped[str] = mapped_column(String(16), default="auto")
    # Who named the merchant: None is the bank, "rule" a rule's rename, "manual" you.
    merchant_source: Mapped[str | None] = mapped_column(String(8))
    # The bank's name for it, kept while a rule or you have renamed it.
    bank_merchant: Mapped[str | None] = mapped_column(String(160))
    bank_merchant_key: Mapped[str | None] = mapped_column(String(160))
    provider_category: Mapped[str | None] = mapped_column(String(64))
    mcc: Mapped[str | None] = mapped_column(String(8))
    # card|direct_debit|standing_order|transfer|cash|interest|fee|internal|dividend,
    # or None when the provider doesn't say (statement imports, older rows).
    payment_type: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="booked")  # booked|pending
    is_transfer: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    transfer_group: Mapped[str | None] = mapped_column(String(64))
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    # Who decided whether it counts: "rule" or "manual"; None when nobody has.
    excluded_source: Mapped[str | None] = mapped_column(String(8))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    account: Mapped[Account] = relationship(back_populates="transactions")


class CategoryRule(Base):
    """One of your rules: when a transaction meets every condition set here,
    the actions apply. Named for its first job, setting a category.

    Conditions (at least one): text in the merchant's name or the
    description, money in or out, the size of the amount, the account, the
    kind of payment. Actions (at least one): a category, a new name for the
    merchant, leaving it out of the totals.
    """

    __tablename__ = "category_rules"
    # One rule per set of conditions: saving the same conditions again changes it.
    __table_args__ = (UniqueConstraint("signature", name="uq_rule_signature"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # --- conditions
    pattern: Mapped[str] = mapped_column(String(160), default="")  # "" is no text condition
    match_type: Mapped[str] = mapped_column(String(16), default="contains")  # contains|words|regex|exact
    field: Mapped[str] = mapped_column(String(16), default="merchant")  # merchant|description
    direction: Mapped[str | None] = mapped_column(String(4))  # in|out
    # The size of the amount, in pence, whichever way the money went.
    amount_min_minor: Mapped[int | None] = mapped_column(Integer)
    amount_max_minor: Mapped[int | None] = mapped_column(Integer)
    account_id: Mapped[int | None] = mapped_column(Integer)
    payment_type: Mapped[str | None] = mapped_column(String(16))  # see categorise.PAYMENT_TYPES
    # --- actions
    category: Mapped[str] = mapped_column(String(48), default="")  # "" leaves the category alone
    rename_to: Mapped[str | None] = mapped_column(String(160))
    exclude: Mapped[bool] = mapped_column(Boolean, default=False)

    priority: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # The conditions as one string, so the database itself refuses a second
    # rule with the same ones. Filled in on every save.
    signature: Mapped[str] = mapped_column(String(512))


def rule_signature(
    *,
    pattern: str | None,
    match_type: str | None,
    field: str | None,
    direction: str | None = None,
    amount_min_minor: int | None = None,
    amount_max_minor: int | None = None,
    account_id: int | None = None,
    payment_type: str | None = None,
) -> str:
    """The identity of a rule's conditions. Migration 6 fills it in for rules
    saved before it existed, so changing this needs a migration too."""
    return json.dumps(
        [
            field or "merchant",
            match_type or "contains",
            pattern or "",
            direction,
            amount_min_minor,
            amount_max_minor,
            account_id,
            payment_type,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


@event.listens_for(CategoryRule, "before_insert")
@event.listens_for(CategoryRule, "before_update")
def _sign_rule(_mapper, _connection, rule: CategoryRule) -> None:
    rule.signature = rule_signature(
        pattern=rule.pattern,
        match_type=rule.match_type,
        field=rule.field,
        direction=rule.direction,
        amount_min_minor=rule.amount_min_minor,
        amount_max_minor=rule.amount_max_minor,
        account_id=rule.account_id,
        payment_type=rule.payment_type,
    )


class Budget(Base):
    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(48), unique=True)
    monthly_limit_minor: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ManualAsset(Base):
    """Things with no API: the car, the pension, a property, a private loan."""

    __tablename__ = "manual_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16), default="asset")  # asset|liability
    category: Mapped[str] = mapped_column(String(32), default="other")  # car|pension|property|other
    value_minor: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    # Optional straight-line depreciation, handy for the car.
    annual_change_pct: Mapped[float | None] = mapped_column()
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class NetWorthSnapshot(Base):
    __tablename__ = "networth_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    taken_on: Mapped[dt.date] = mapped_column(Date, unique=True)
    assets_minor: Mapped[int] = mapped_column(Integer, default=0)
    liabilities_minor: Mapped[int] = mapped_column(Integer, default=0)
    net_minor: Mapped[int] = mapped_column(Integer, default=0)
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict)


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    connection_id: Mapped[int | None] = mapped_column(ForeignKey("connections.id"))
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|ok|error
    accounts_seen: Mapped[int] = mapped_column(Integer, default=0)
    transactions_added: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text)


class DismissedInsight(Base):
    """Insight keys the user has ticked off, so they stop reappearing."""

    __tablename__ = "dismissed_insights"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(160), unique=True)
    dismissed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
