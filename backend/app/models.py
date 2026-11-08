"""SQLAlchemy models.

Amount convention: `amount_minor` is signed pence from the account holder's
point of view — negative is money leaving, positive is money arriving. This
holds for credit cards too (a purchase on Amex is negative), so spend analysis
never has to special-case an account type.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
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
    __table_args__ = (UniqueConstraint("account_id", "external_id", name="uq_txn_account_ext"),)

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
    category_source: Mapped[str] = mapped_column(String(16), default="auto")  # auto|rule|manual
    provider_category: Mapped[str | None] = mapped_column(String(64))
    mcc: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), default="booked")  # booked|pending
    is_transfer: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    transfer_group: Mapped[str | None] = mapped_column(String(64))
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    account: Mapped[Account] = relationship(back_populates="transactions")


class CategoryRule(Base):
    """User-defined override: "anything matching X is category Y"."""

    __tablename__ = "category_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    pattern: Mapped[str] = mapped_column(String(160))
    match_type: Mapped[str] = mapped_column(String(16), default="contains")  # contains|regex|exact
    field: Mapped[str] = mapped_column(String(16), default="merchant")  # merchant|description
    category: Mapped[str] = mapped_column(String(48))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


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
