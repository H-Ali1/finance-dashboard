"""Trading 212 — read-only valuation of Invest and Stocks ISA accounts.

In the Trading 212 app: Settings -> API -> Generate API key. Each key belongs to
one account (Invest or Stocks ISA), comes with a secret, and needs the
"account" and "portfolio" read permissions. Up to two accounts are supported
(trading212_api_key/secret and trading212_api_key_2/secret_2).

Requests use HTTP Basic auth with the key as username and the secret as
password. A key on its own is sent the old way (the bare key in the
Authorization header), for keys made before secrets existed.

Rate limits are per account: summary 1 request / 5 s, positions 1 / s. A sync
makes one call to each.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

import httpx

from ..config import settings
from ..money import to_minor
from .base import ProviderError, RawAccount

TIMEOUT = httpx.Timeout(30.0)


@dataclass(frozen=True)
class Credentials:
    key: str
    secret: str = ""

    @property
    def header(self) -> str:
        if not self.secret:
            return self.key  # legacy single-key scheme
        token = base64.b64encode(f"{self.key}:{self.secret}".encode()).decode()
        return f"Basic {token}"


def configured_credentials() -> list[Credentials]:
    pairs = [
        (settings.trading212_api_key, settings.trading212_api_secret),
        (settings.trading212_api_key_2, settings.trading212_api_secret_2),
    ]
    return [Credentials(key.strip(), secret.strip()) for key, secret in pairs if key.strip()]


def _get(creds: Credentials, path: str) -> dict | list:
    resp = httpx.get(
        f"{settings.trading212_base_url.rstrip('/')}{path}",
        headers={"Authorization": creds.header, "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    if resp.status_code == 401:
        raise ProviderError(
            "Trading 212 rejected the API key"
            + (" and secret" if creds.secret else " (newer keys also need their API secret)")
            + ". Check them, or generate a new key in the app."
        )
    if resp.status_code == 403:
        raise ProviderError(
            f"The Trading 212 key is missing a permission for {path}. Generate a key with the "
            "'account' and 'portfolio' read permissions."
        )
    if resp.status_code == 429:
        raise ProviderError("Trading 212 rate limit hit — it'll be retried shortly.")
    if resp.status_code >= 400:
        raise ProviderError(f"Trading 212 {path} failed ({resp.status_code}): {resp.text[:200]}")
    return resp.json()


def _number(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _holding(position: dict) -> dict:
    instrument = position.get("instrument") or {}
    wallet = position.get("walletImpact") or {}
    return {
        "ticker": instrument.get("ticker") or "?",
        "name": instrument.get("name") or "",
        "quantity": _number(position.get("quantity")),
        "average_price": _number(position.get("averagePricePaid")),
        "current_price": _number(position.get("currentPrice")),
        # Prices are in the share's own currency (USD for US stocks)...
        "price_currency": instrument.get("currency") or "",
        # ...but value and P/L come back in the account's currency.
        "value": round(_number(wallet.get("currentValue")), 2),
        "cost": round(_number(wallet.get("totalCost")), 2),
        "ppl": round(_number(wallet.get("unrealizedProfitLoss")), 2),
    }


def fetch_account(creds: Credentials) -> RawAccount:
    summary = _get(creds, "/equity/account/summary")
    if not isinstance(summary, dict):
        raise ProviderError("Trading 212 returned an unexpected account summary")
    positions = _get(creds, "/equity/positions")
    holdings = [_holding(p) for p in positions] if isinstance(positions, list) else []

    cash = summary.get("cash") or {}
    investments = summary.get("investments") or {}
    account_id = str(summary.get("id") or creds.key[-6:])
    currency = summary.get("currency") or settings.base_currency
    return RawAccount(
        external_id=f"trading212-{account_id}",
        name=f"Trading 212 ••{account_id[-4:]}",
        kind="investment",
        currency=currency,
        balance_minor=to_minor(summary.get("totalValue")),
        available_minor=to_minor(cash.get("availableToTrade")),
        institution_name="Trading 212",
        meta={
            "account_id": account_id,
            "cash": cash,
            "invested": investments.get("totalCost"),
            "investments_value": investments.get("currentValue"),
            "unrealised_pl": investments.get("unrealizedProfitLoss"),
            "realised_pl": investments.get("realizedProfitLoss"),
            "holdings": sorted(holdings, key=lambda h: h["value"], reverse=True),
        },
    )


def fetch_accounts() -> list[RawAccount]:
    creds = configured_credentials()
    if not creds:
        raise ProviderError("No Trading 212 API key is configured")
    return [fetch_account(c) for c in creds]
