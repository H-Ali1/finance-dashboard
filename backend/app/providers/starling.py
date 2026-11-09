"""Starling Bank — direct personal access token.

Create a token at https://developer.starlingbank.com (Personal Access) with the
read-only scopes: account:read, account-list:read, balance:read,
transaction:read, savings-goal:read, space:read. Tokens don't expire (100-year
lifetime) and are revoked from the developer portal.
"""

from __future__ import annotations

import datetime as dt
import re

import httpx

from ..config import settings
from .base import ProviderError, RawAccount, RawTransaction

TIMEOUT = httpx.Timeout(30.0)

# Starling caps how long a period one transactions request may cover, and
# doesn't publish the cap. History is read backwards from today in windows of
# this size, halved whenever Starling says a window is too long.
FEED_WINDOW = dt.timedelta(days=90)
MIN_FEED_WINDOW = dt.timedelta(days=7)


class RangeTooLong(ProviderError):
    """Starling refused a transactions request for covering too long a period."""


class RateLimited(ProviderError):
    """Starling is limiting requests (HTTP 429). Not a fault: try again later."""


_UID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


def _label(path: str) -> str:
    """The request without account and category IDs, for messages."""
    return _UID.sub("…", path)


def _client() -> httpx.Client:
    if not settings.starling_configured:
        raise ProviderError("STARLING_ACCESS_TOKEN is not set in backend/.env")
    return httpx.Client(
        base_url=settings.starling_base_url,
        headers={
            "Authorization": f"Bearer {settings.starling_access_token}",
            "Accept": "application/json",
            "User-Agent": "finance-dashboard/1.0",
        },
        timeout=TIMEOUT,
    )


def _get(client: httpx.Client, path: str, **params) -> dict:
    resp = client.get(path, params=params or None)
    if resp.status_code in (401, 403):
        raise ProviderError(
            f"Starling rejected the token ({resp.status_code}). Check it has not been revoked and "
            "that it carries the account/balance/transaction read scopes."
        )
    if resp.status_code == 400 and "QUERY_EXCEEDING_MAX_TIME_RANGE" in resp.text:
        raise RangeTooLong(f"Starling {_label(path)}: period too long")
    if resp.status_code == 429:
        raise RateLimited("Starling is limiting requests for now. The next scheduled sync will catch up.")
    if resp.status_code >= 400:
        raise ProviderError(f"Starling {_label(path)} failed: {resp.status_code} {resp.text[:200]}")
    return resp.json()


def _amount_minor(node: dict | None) -> int:
    if not node:
        return 0
    return int(node.get("minorUnits") or 0)


def fetch_accounts() -> list[RawAccount]:
    """Current accounts plus each savings space, so net worth sees the lot."""
    out: list[RawAccount] = []
    with _client() as client:
        for acc in _get(client, "/api/v2/accounts").get("accounts", []):
            uid = acc["accountUid"]
            balance = _get(client, f"/api/v2/accounts/{uid}/balance")
            currency = acc.get("currency", "GBP")
            out.append(
                RawAccount(
                    external_id=uid,
                    name=acc.get("name") or "Starling account",
                    kind="current",
                    currency=currency,
                    balance_minor=_amount_minor(balance.get("effectiveBalance")),
                    available_minor=_amount_minor(balance.get("availableToSpend")),
                    institution_name="Starling",
                    meta={"default_category": acc.get("defaultCategory")},
                )
            )

            try:
                goals = _get(client, f"/api/v2/account/{uid}/savings-goals").get("savingsGoalList", [])
            except RateLimited:
                raise  # wait for the next sync rather than report no savings
            except ProviderError:
                goals = []  # scope not granted — not fatal
            for goal in goals:
                saved = _amount_minor(goal.get("totalSaved"))
                if saved == 0:
                    continue
                out.append(
                    RawAccount(
                        external_id=f"{uid}:goal:{goal['savingsGoalUid']}",
                        name=f"Space — {goal.get('name', 'Savings')}",
                        kind="savings",
                        currency=goal.get("totalSaved", {}).get("currency", currency),
                        balance_minor=saved,
                        institution_name="Starling",
                        meta={"savings_goal": True, "parent_account": uid},
                    )
                )
    return out


def fetch_transactions(account: RawAccount | dict, since: dt.date) -> list[RawTransaction]:
    meta = account.meta if isinstance(account, RawAccount) else (account.get("meta") or {})
    external_id = account.external_id if isinstance(account, RawAccount) else account["external_id"]
    if meta.get("savings_goal"):
        return []  # spaces have no transaction feed of their own

    category = meta.get("default_category")
    if not category:
        raise ProviderError(f"No Starling category uid stored for account {external_id}")

    start = dt.datetime.combine(since, dt.time.min, tzinfo=dt.timezone.utc)
    end = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
    path = f"/api/v2/feed/account/{external_id}/category/{category}/transactions-between"

    items: dict[str, dict] = {}
    with _client() as client:
        window_end, window = end, FEED_WINDOW
        while window_end > start:
            window_start = max(start, window_end - window)
            try:
                payload = _get(
                    client,
                    path,
                    minTransactionTimestamp=_iso(window_start),
                    maxTransactionTimestamp=_iso(window_end),
                )
            except RangeTooLong:
                if window > MIN_FEED_WINDOW:
                    window = max(MIN_FEED_WINDOW, window / 2)
                    continue
                if window_end == end:
                    raise ProviderError("Starling refused even a one-week transactions request") from None
                break  # as far back as Starling will go; keep what we have
            for item in payload.get("feedItems", []):
                items[item["feedItemUid"]] = item  # windows share their edges
            window_end = window_start

    out: list[RawTransaction] = []
    for item in items.values():
        status = (item.get("status") or "").upper()
        # Cancelled payments are passed on, so a pending copy stored earlier is removed.
        cancelled = status in {"DECLINED", "REJECTED", "REVERSED"}
        minor = _amount_minor(item.get("amount"))
        signed = -minor if item.get("direction") == "OUT" else minor
        when = _parse_ts(item.get("transactionTime") or item.get("updatedAt"))
        counterparty = item.get("counterPartyName") or ""
        reference = item.get("reference") or ""
        payment_type = _payment_type(item.get("source"), item.get("sourceSubType"))
        out.append(
            RawTransaction(
                external_id=item["feedItemUid"],
                booked_on=when.date(),
                booked_at=when,
                amount_minor=signed,
                currency=item.get("amount", {}).get("currency", "GBP"),
                description=" ".join(p for p in (counterparty, reference) if p).strip(),
                merchant=counterparty or reference,
                provider_category=item.get("spendingCategory"),
                status="reversed" if cancelled else "pending" if status == "PENDING" else "booked",
                payment_type=payment_type,
            )
        )
    return out


# Starling's `source`: how the money moved.
_SOURCES = {
    "MASTER_CARD": "card",
    "MASTERCARD_CHARGEBACK": "card",
    "DIRECT_DEBIT": "direct_debit",
    "DIRECT_DEBIT_DISPUTE": "direct_debit",
    "SEPA_DIRECT_DEBIT": "direct_debit",
    "FASTER_PAYMENTS_IN": "transfer",
    "FASTER_PAYMENTS_OUT": "transfer",
    "FASTER_PAYMENTS_REVERSAL": "transfer",
    "FASTER_PAYMENTS_REFUND": "transfer",
    "DIRECT_CREDIT": "transfer",  # BACS, which is how most salaries arrive
    "CHAPS": "transfer",
    "ON_US_PAY_ME": "transfer",
    "SEPA_CREDIT_TRANSFER": "transfer",
    "SEPA_INSTANT_CREDIT_TRANSFER": "transfer",
    "INTERNAL_TRANSFER": "internal",
    "INTEREST_PAYMENT": "interest",
    "OVERDRAFT": "interest",
    "CARD_FEE_CHARGE": "fee",
    "CASH_DEPOSIT_CHARGE": "fee",
    "CASH_WITHDRAWAL_CHARGE": "fee",
    "MISSED_PAYMENT_FEE": "fee",
}


def _payment_type(source: str | None, sub_type: str | None) -> str | None:
    sub = (sub_type or "").upper()
    if sub == "ATM":
        return "cash"
    if sub == "STANDING_ORDER":
        return "standing_order"
    return _SOURCES.get((source or "").upper())


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _parse_ts(value: str | None) -> dt.datetime:
    if not value:
        return dt.datetime.now(dt.timezone.utc)
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
