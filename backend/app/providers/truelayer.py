"""TrueLayer: UK banks through open banking (HSBC, Amex, first direct, ...).

TrueLayer only signs contracts with businesses. An app switched to Live in
TrueLayer Console without one still connects real banks, with a "test mode"
notice on the consent screen. TrueLayer doesn't support that use and could end
it at any time; Starling and Trading 212 don't depend on it.

Connecting a bank:
  1. auth_link() opens TrueLayer, then the bank, to approve read-only access.
  2. TrueLayer's redirect page shows a code. It must be pasted back within
     5 minutes.
  3. exchange_code() swaps it for tokens. Only the refresh token is kept.

Access lasts 90 days. After that the bank is connected again the same way, and
the existing accounts and transactions are matched rather than duplicated.

UK banks allow four unattended refreshes a day per account. Requests made
while you're using the dashboard can pass your IP address (X-PSU-IP), which
lifts that limit; the scheduled sync stays within it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx

from ..config import settings
from ..money import to_minor
from .base import ProviderError, RawAccount, RawTransaction

TIMEOUT = httpx.Timeout(30.0)
# What each bank is asked for, cut down to what that bank offers (Amex has no
# `accounts`, only `cards`). No `info`: the dashboard has no use for your name
# and address.
WANTED_SCOPES = ("accounts", "cards", "balance", "transactions", "offline_access")
# Days of history asked for when an account is new. Many banks only release
# more than 90 days in the first minutes after you approve, and some never do;
# a bank that refuses is asked for less.
HISTORY_DAYS = (730, 365, 89)


class ConsentExpired(ProviderError):
    """The 90 days ran out, or access was revoked at the bank. Connect it again."""


class HistoryRefused(ProviderError):
    """The bank won't return transactions that far back."""


class RateLimited(ProviderError):
    """The bank's limit on refreshes (four a day when you're not present)."""


class NotSupported(ProviderError):
    """The bank doesn't offer this endpoint."""


@dataclass(slots=True)
class Tokens:
    access_token: str
    refresh_token: str | None
    expires_in: int


@dataclass(slots=True)
class ConnectionInfo:
    provider_id: str
    provider_name: str
    credentials_id: str | None
    consent_expires_at: dt.datetime | None
    scopes: list[str]


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT,
        headers={"Accept": "application/json", "User-Agent": "finance-dashboard/1.0"},
    )


def _require_configured() -> None:
    if not settings.truelayer_configured:
        raise ProviderError(
            "TrueLayer isn't set up: add the client ID and client secret from TrueLayer Console "
            "(Live, not Sandbox) to the App's Configuration tab"
        )


def _error_code(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return ""
    if isinstance(body, dict):
        return str(body.get("error") or body.get("type") or "")
    return ""


def _error_text(resp: httpx.Response) -> str:
    """The error code and description only. Never echo a whole body back:
    token responses carry secrets."""
    try:
        body = resp.json()
    except ValueError:
        return f"{resp.status_code}"
    if not isinstance(body, dict):
        return f"{resp.status_code}"
    parts = [str(body.get(k)) for k in ("error", "error_description") if body.get(k)]
    return f"{resp.status_code} {': '.join(parts)}".strip()


# --- choosing a bank and approving access ------------------------------------
def banks() -> list[dict]:
    """UK banks TrueLayer can connect, with what each one shares."""
    sandbox = "sandbox" in settings.truelayer_auth_url
    url = f"{settings.truelayer_auth_url}/api/providers"
    with _client() as client:
        resp = client.get(url, params={"country": "uk", "client_id": settings.truelayer_client_id})
        if resp.status_code >= 400:  # e.g. an unknown client ID: show the public list
            resp = client.get(url, params={"country": "uk"})
    if resp.status_code >= 400:
        raise ProviderError(f"TrueLayer's list of banks failed: {_error_text(resp)}")
    out = []
    for provider in resp.json():
        scopes = provider.get("scopes") or []
        provider_id = provider.get("provider_id") or ""
        if "transactions" not in scopes or (provider_id.startswith("mock") and not sandbox):
            continue
        out.append(
            {
                "provider_id": provider_id,
                "name": provider.get("display_name") or provider_id,
                "logo_url": provider.get("logo_url"),
                "scopes": scopes,
            }
        )
    return sorted(out, key=lambda p: p["name"].lower())


def auth_link(provider_id: str, provider_scopes: list[str] | None = None) -> str:
    """Where to send you to approve access to one bank."""
    _require_configured()
    offered = set(provider_scopes) if provider_scopes else set(WANTED_SCOPES)
    scopes = [s for s in WANTED_SCOPES if s in offered]
    if "transactions" not in scopes:
        raise ProviderError(f"{provider_id} doesn't share transactions through TrueLayer")
    query = {
        "response_type": "code",
        "client_id": settings.truelayer_client_id,
        "redirect_uri": settings.truelayer_redirect_uri,
        "scope": " ".join(scopes),
        # Skips TrueLayer's bank picker; the bank must also be in `providers`.
        "providers": provider_id,
        "provider_id": provider_id,
        "state": secrets.token_urlsafe(16),
    }
    return f"{settings.truelayer_auth_url}/?{urlencode(query, quote_via=quote)}"


def parse_code(pasted: str) -> str:
    """The code from whatever was pasted: the code itself, or the whole
    address of TrueLayer's redirect page."""
    text = (pasted or "").strip()
    if "?" in text or "code=" in text or "error=" in text:
        query = urlparse(text).query or text.split("?", 1)[-1]
        params = parse_qs(query)
        if params.get("error"):
            raise ProviderError(f"The bank or TrueLayer stopped the connection: {params['error'][0]}")
        if params.get("code"):
            return params["code"][0].strip()
    if not text or any(c.isspace() for c in text):
        raise ProviderError("Paste the code TrueLayer showed after you approved access")
    return text


def _token_request(form: dict, *, exchanging: bool) -> Tokens:
    _require_configured()
    with _client() as client:
        resp = client.post(
            f"{settings.truelayer_auth_url}/connect/token",
            data={
                **form,
                "client_id": settings.truelayer_client_id,
                "client_secret": settings.truelayer_client_secret,
            },
        )
    if resp.status_code >= 400:
        code = _error_code(resp)
        if code in ("invalid_client", "unauthorized_client"):
            raise ProviderError(
                "TrueLayer rejected the client ID or secret. Use the Live ones: the client ID "
                "doesn't start with sandbox-"
            )
        if exchanging and code in ("invalid_grant", "invalid_authorization_code"):
            raise ProviderError(
                "TrueLayer didn't accept that code. Codes last 5 minutes and work once, and the "
                "redirect URI in TrueLayer Console must match exactly. Start again from Connect."
            )
        if code == "invalid_grant":
            raise ConsentExpired("Access to this bank has ended (90 days pass, or it was revoked). Connect it again.")
        raise ProviderError(f"TrueLayer token request failed: {_error_text(resp)}")
    body = resp.json()
    return Tokens(
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token"),
        expires_in=int(body.get("expires_in") or 3600),
    )


def exchange_code(code: str) -> Tokens:
    tokens = _token_request(
        {"grant_type": "authorization_code", "code": code, "redirect_uri": settings.truelayer_redirect_uri},
        exchanging=True,
    )
    if not tokens.refresh_token:
        raise ProviderError("TrueLayer gave no refresh token, so this bank couldn't be read again later")
    return tokens


def refresh(refresh_token: str) -> Tokens:
    return _token_request({"grant_type": "refresh_token", "refresh_token": refresh_token}, exchanging=False)


def delete_credential(access_token: str) -> None:
    """Tell TrueLayer to drop its access to the bank. Best effort."""
    try:
        with _client() as client:
            client.delete(
                f"{settings.truelayer_auth_url}/api/delete",
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.HTTPError:
        pass


# --- reading data -------------------------------------------------------------
def _get(client: httpx.Client, token: str, path: str, psu_ip: str | None = None, **params) -> list[dict]:
    headers = {"Authorization": f"Bearer {token}"}
    if psu_ip:
        headers["X-PSU-IP"] = psu_ip
    try:
        resp = client.get(f"{settings.truelayer_api_url}/data/v1{path}", headers=headers, params=params or None)
    except httpx.HTTPError as exc:
        raise ProviderError(f"Couldn't reach TrueLayer: {exc}") from exc
    if resp.status_code == 200:
        return resp.json().get("results") or []

    code = _error_code(resp)
    if code == "access_denied":
        raise ConsentExpired(
            "The bank has stopped sharing data (90 days passed, or access was revoked). Connect it again."
        )
    if code in ("sca_exceeded", "invalid_date_range"):
        raise HistoryRefused(f"TrueLayer {path}: {_error_text(resp)}")
    if resp.status_code == 429:
        raise RateLimited("The bank only allows a few automatic refreshes a day. It'll catch up at the next sync.")
    if resp.status_code == 501 or code == "endpoint_not_supported":
        raise NotSupported(f"TrueLayer {path}: not offered by this bank")
    raise ProviderError(f"TrueLayer {path} failed: {_error_text(resp)}")


def _optional(client: httpx.Client, token: str, path: str, psu_ip: str | None) -> list[dict]:
    try:
        return _get(client, token, path, psu_ip)
    except NotSupported:
        return []


def connection_info(access_token: str, psu_ip: str | None = None) -> ConnectionInfo:
    """Which bank a token is for, and when access ends."""
    with _client() as client:
        results = _get(client, access_token, "/me", psu_ip)
    if not results:
        raise ProviderError("TrueLayer didn't say which bank this connection is for")
    me = results[0]
    provider = me.get("provider") or {}
    expires = me.get("consent_expires_at")
    return ConnectionInfo(
        provider_id=provider.get("provider_id") or "",
        provider_name=provider.get("display_name") or provider.get("provider_id") or "Bank",
        credentials_id=me.get("credentials_id"),
        consent_expires_at=_parse_ts(expires) if expires else None,
        scopes=list(me.get("scopes") or []),
    )


def _stable_id(provider_id: str, *parts: str | None) -> str:
    """An account key that survives reconnecting, when TrueLayer issues new
    account IDs. The account number is hashed, not stored."""
    key = "|".join(p for p in parts if p)
    return f"{provider_id}:{hashlib.sha256(key.encode()).hexdigest()[:20]}"


def _first(results: list[dict]) -> dict:
    return results[0] if results else {}


def fetch_accounts(
    access_token: str, scopes: list[str], psu_ip: str | None = None, bank_name: str = ""
) -> list[RawAccount]:
    out: list[RawAccount] = []
    with _client() as client:
        if "accounts" in scopes:
            for acc in _optional(client, access_token, "/accounts", psu_ip):
                balance = _first(_optional(client, access_token, f"/accounts/{acc['account_id']}/balance", psu_ip))
                out.append(_raw_account(acc, balance, bank_name))
        if "cards" in scopes:
            for card in _optional(client, access_token, "/cards", psu_ip):
                balance = _first(_optional(client, access_token, f"/cards/{card['account_id']}/balance", psu_ip))
                out.append(_raw_card(card, balance, bank_name))
    return out


def _raw_account(acc: dict, balance: dict, bank_name: str) -> RawAccount:
    provider_id = (acc.get("provider") or {}).get("provider_id") or ""
    number = acc.get("account_number") or {}
    sort_code = (number.get("sort_code") or "").replace("-", "")
    stable = _stable_id(
        provider_id, sort_code + (number.get("number") or "") or number.get("iban") or acc["account_id"]
    )
    available = balance.get("available")
    return RawAccount(
        external_id=stable,
        name=acc.get("display_name") or "Account",
        kind="savings" if "SAVINGS" in (acc.get("account_type") or "") else "current",
        currency=acc.get("currency") or balance.get("currency") or "GBP",
        balance_minor=to_minor(balance.get("current")),
        available_minor=to_minor(available) if available is not None else None,
        institution_name=bank_name,
        meta={"truelayer_id": acc["account_id"], "truelayer_kind": "accounts"},
    )


def _raw_card(card: dict, balance: dict, bank_name: str) -> RawAccount:
    provider_id = (card.get("provider") or {}).get("provider_id") or ""
    partial = card.get("partial_card_number") or ""
    available = balance.get("available")
    name = card.get("display_name") or (f"Card ending {partial[-4:]}" if partial else "Card")
    return RawAccount(
        external_id=_stable_id(provider_id, "card", partial or card["account_id"]),
        name=name,
        kind="credit_card",
        currency=card.get("currency") or balance.get("currency") or "GBP",
        # Card balances are what you owe, as a positive number. Here money owed
        # is negative, the same as a purchase.
        balance_minor=-to_minor(balance.get("current")),
        available_minor=to_minor(available) if available not in (None, "") else None,
        institution_name=bank_name,
        meta={"truelayer_id": card["account_id"], "truelayer_kind": "cards"},
    )


def fetch_transactions(
    access_token: str, account: RawAccount | dict, since: dt.date, psu_ip: str | None = None
) -> list[RawTransaction]:
    meta = (
        account.meta if isinstance(account, RawAccount) else (account.get("provider_meta") or account.get("meta") or {})
    )
    kind, tl_id = meta.get("truelayer_kind"), meta.get("truelayer_id")
    if kind not in ("accounts", "cards") or not tl_id:
        raise ProviderError("This account has no TrueLayer ID stored; connect the bank again")
    path = f"/{kind}/{tl_id}/transactions"
    today = dt.date.today()
    start = since
    with _client() as client:
        while True:
            try:
                rows = _get(client, access_token, path, psu_ip, **{"from": start.isoformat(), "to": today.isoformat()})
                break
            except HistoryRefused:
                shorter = [today - dt.timedelta(days=d) for d in HISTORY_DAYS if today - dt.timedelta(days=d) > start]
                if not shorter:
                    raise ProviderError("The bank refused even the last 89 days of transactions") from None
                start = shorter[0]
    return [_raw_transaction(row, is_card=kind == "cards") for row in rows]


def _raw_transaction(row: dict, *, is_card: bool) -> RawTransaction:
    amount = to_minor(row.get("amount"))
    kind = (row.get("transaction_type") or "").upper()
    if kind == "DEBIT":
        signed = -abs(amount)
    elif kind == "CREDIT":
        signed = abs(amount)
    else:
        # Accounts sign amounts already; cards report purchases as positive.
        signed = -amount if is_card else amount
    when = _parse_ts(row.get("timestamp"))
    description = (row.get("description") or "").strip()
    meta = row.get("meta") or {}
    merchant = (
        row.get("merchant_name") or meta.get("provider_merchant_name") or meta.get("counter_party_preferred_name") or ""
    )
    ident = row.get("normalised_provider_transaction_id") or row.get("provider_transaction_id") or row["transaction_id"]
    return RawTransaction(
        external_id=f"tl:{ident}",
        booked_on=when.date(),
        booked_at=when,
        amount_minor=signed,
        currency=row.get("currency") or "GBP",
        description=description,
        merchant=merchant or description,
        provider_category=_category(row.get("transaction_classification") or []),
        payment_type=_payment_type(row.get("transaction_category"), meta.get("provider_transaction_category")),
    )


# TrueLayer's transaction_category, and the bank's own code where TrueLayer's
# is only "debit" or "credit" (HSBC sends "SO", "DD", "VIS", ")))", ...).
_TL_TYPES = {
    "PURCHASE": "card",
    "DIRECT_DEBIT": "direct_debit",
    "STANDING_ORDER": "standing_order",
    "BILL_PAYMENT": "transfer",
    "TRANSFER": "transfer",
    "ATM": "cash",
    "CASH": "cash",
    "INTEREST": "interest",
    "FEE_CHARGE": "fee",
    "DIVIDEND": "dividend",
}
_BANK_CODES = {
    "VIS": "card",
    ")))": "card",
    "POS": "card",
    "CARD": "card",
    "DD": "direct_debit",
    "DDR": "direct_debit",
    "SO": "standing_order",
    "STO": "standing_order",
    "BP": "transfer",
    "FPI": "transfer",
    "FPO": "transfer",
    "TFR": "transfer",
    "BGC": "transfer",
    "OBP": "transfer",
    "CR": "transfer",
    "ATM": "cash",
    "CPT": "cash",
    "INT": "interest",
    "FEE": "fee",
    "CHG": "fee",
}


def _payment_type(category: str | None, bank_code: str | None) -> str | None:
    by_category = _TL_TYPES.get((category or "").strip().upper())
    return by_category or _BANK_CODES.get((bank_code or "").strip().upper())


# TrueLayer's classification, e.g. ["Auto & Transport", "Gas & Fuel"], as our
# category names. The detail is checked before the group, so fuel is Fuel
# rather than Transport and rent is Rent & Mortgage rather than Home.
_DETAIL = {
    "groceries": "Groceries",
    "gas and fuel": "Fuel",
    "fuel": "Fuel",
    "parking": "Transport",
    "public transport": "Transport",
    "taxi": "Transport",
    "auto insurance": "Insurance",
    "rent": "Rent & Mortgage",
    "mortgage": "Rent & Mortgage",
    "mortgage and rent": "Rent & Mortgage",
    "life insurance": "Insurance",
    "health insurance": "Insurance",
    "buildings and contents insurance": "Insurance",
    "pension payments": "Savings & Investments",
    "tuition": "Education",
    "student loan": "Education",
    "charity": "Gifts & Charity",
    "television": "Bills & Utilities",
    "internet": "Bills & Utilities",
    "mobile phone": "Bills & Utilities",
    "home phone": "Bills & Utilities",
    "utilities": "Bills & Utilities",
    "air travel": "Travel",
    "hotel": "Travel",
    "gym": "Health & Fitness",
    "pharmacy": "Health & Fitness",
}
_GROUP = {
    "food and dining": "Eating Out",
    "auto and transport": "Transport",
    "shopping": "Shopping",
    "bills and utilities": "Bills & Utilities",
    "entertainment": "Entertainment",
    "travel": "Travel",
    "personal care": "Health & Fitness",
    "health and fitness": "Health & Fitness",
    "gifts and donations": "Gifts & Charity",
    "home": "Shopping",
    "investments": "Savings & Investments",
    "education": "Education",
    "fees and charges": "Fees & Interest",
    "taxes": "Tax",
    "gambling": "Entertainment",
    "pension and insurances": "Insurance",
}


def _category(classification: list[str]) -> str | None:
    names = [c.lower().replace("&", "and").strip() for c in classification if c]
    for name in names:
        if name in _DETAIL:
            return _DETAIL[name]
    return _GROUP.get(names[0]) if names else None


def _parse_ts(value: str | None) -> dt.datetime:
    if not value:
        return dt.datetime.now(dt.timezone.utc)
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
