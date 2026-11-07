"""Trading 212 — read-only valuation of the Invest/ISA account.

Generate a key in the app: Settings -> API (Beta) -> Generate API key, with the
read scopes only. The API is rate limited hard (roughly one call per 5s per
endpoint), so this is only touched on an explicit sync.
"""

from __future__ import annotations

import httpx

from ..config import settings
from ..money import to_minor
from .base import ProviderError, RawAccount

TIMEOUT = httpx.Timeout(30.0)


def _get(path: str) -> dict | list:
    if not settings.trading212_configured:
        raise ProviderError("TRADING212_API_KEY is not set in backend/.env")
    resp = httpx.get(
        f"{settings.trading212_base_url}{path}",
        headers={"Authorization": settings.trading212_api_key, "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    if resp.status_code == 401:
        raise ProviderError("Trading 212 rejected the API key (401). Regenerate it in the app.")
    if resp.status_code == 429:
        raise ProviderError("Trading 212 rate limit hit — wait a minute and sync again.")
    if resp.status_code >= 400:
        raise ProviderError(f"Trading 212 {path} failed ({resp.status_code}): {resp.text[:200]}")
    return resp.json()


def fetch_accounts() -> list[RawAccount]:
    cash = _get("/equity/account/cash")
    assert isinstance(cash, dict)
    try:
        positions = _get("/equity/portfolio")
        assert isinstance(positions, list)
    except ProviderError:
        positions = []

    holdings = [
        {
            "ticker": p.get("ticker"),
            "quantity": p.get("quantity"),
            "average_price": p.get("averagePrice"),
            "current_price": p.get("currentPrice"),
            "value": round(float(p.get("quantity") or 0) * float(p.get("currentPrice") or 0), 2),
            "ppl": p.get("ppl"),
        }
        for p in positions
    ]

    return [
        RawAccount(
            external_id="trading212-invest",
            name="Trading 212",
            kind="investment",
            currency=settings.base_currency,
            balance_minor=to_minor(cash.get("total")),
            available_minor=to_minor(cash.get("free")),
            institution_name="Trading 212",
            meta={
                "invested": cash.get("invested"),
                "result": cash.get("result"),
                "ppl": cash.get("ppl"),
                "holdings": sorted(holdings, key=lambda h: h["value"], reverse=True),
            },
        )
    ]
