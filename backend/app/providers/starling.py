"""Starling Bank — direct personal access token.

Create a token at https://developer.starlingbank.com (Personal Access) with the
scopes: account:read, account-identifier:read, balance:read, transaction:read,
savings-goal:read, space:read. Tokens are long-lived but revocable from the app.
"""

from __future__ import annotations

import datetime as dt

import httpx

from ..config import settings
from .base import ProviderError, RawAccount, RawTransaction

TIMEOUT = httpx.Timeout(30.0)


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
    if resp.status_code == 403:
        raise ProviderError(
            "Starling rejected the token (403). Check it has not been revoked and "
            "that it carries the account/balance/transaction read scopes."
        )
    if resp.status_code >= 400:
        raise ProviderError(f"Starling {path} failed: {resp.status_code} {resp.text[:200]}")
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

    with _client() as client:
        payload = _get(
            client,
            path,
            minTransactionTimestamp=_iso(start),
            maxTransactionTimestamp=_iso(end),
        )

    out: list[RawTransaction] = []
    for item in payload.get("feedItems", []):
        status = (item.get("status") or "").upper()
        if status in {"DECLINED", "REJECTED", "REVERSED"}:
            continue
        minor = _amount_minor(item.get("amount"))
        signed = -minor if item.get("direction") == "OUT" else minor
        when = _parse_ts(item.get("transactionTime") or item.get("updatedAt"))
        counterparty = item.get("counterPartyName") or ""
        reference = item.get("reference") or ""
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
                status="pending" if status == "PENDING" else "booked",
            )
        )
    return out


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _parse_ts(value: str | None) -> dt.datetime:
    if not value:
        return dt.datetime.now(dt.timezone.utc)
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
