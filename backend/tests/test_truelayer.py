"""TrueLayer: connecting a bank, reading it, reconnecting, expiry and limits.

A fake TrueLayer answers in the shapes of TrueLayer's Data API v1 reference:
account amounts are signed, card purchases are positive, card balances are
what's owed.
"""

from __future__ import annotations

import datetime as dt
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Account, Connection, Transaction
from app.providers import ProviderError, truelayer
from app.routers.connections import psu_ip
from app.services import sync as sync_service

AUTH = "https://auth.truelayer.com"
API = "https://api.truelayer.com/data/v1"
TODAY = dt.date.today()


def _day(days_ago: int) -> str:
    return (TODAY - dt.timedelta(days=days_ago)).isoformat() + "T00:00:00"


class FakeTrueLayer:
    """One HSBC login with a current account and a credit card.

    `generation` goes up when the bank is connected again: TrueLayer then
    issues new account and transaction IDs, but the account numbers and
    normalised transaction IDs stay the same."""

    def __init__(self, history_days: int = 365):
        self.history_days = history_days
        self.generation = 0
        self.calls: list[tuple[str, str, dict]] = []
        self.refresh_tokens = {"rt-0"}
        self.deleted = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append((request.method, url, dict(request.headers)))
        if url.startswith(f"{AUTH}/connect/token"):
            return self._token(parse_qs(request.content.decode()))
        if url.startswith(f"{AUTH}/api/delete"):
            self.deleted += 1
            return httpx.Response(200, json={})
        if url.startswith(f"{AUTH}/api/providers"):
            return httpx.Response(
                200,
                json=[
                    {"provider_id": "mock", "display_name": "Mock", "scopes": ["accounts", "transactions"]},
                    {"provider_id": "ob-hsbc", "display_name": "HSBC", "scopes": ["info", "accounts", "balance", "transactions", "cards", "offline_access"]},
                    {"provider_id": "ob-amex", "display_name": "American Express", "scopes": ["info", "cards", "balance", "transactions", "offline_access"]},
                ],
            )
        assert request.headers["Authorization"].startswith("Bearer at-"), "data calls need an access token"
        path = url.split("/data/v1", 1)[1].split("?", 1)[0]
        g = self.generation
        if path == "/me":
            return self._ok([{
                "credentials_id": f"cred-{g}",
                "consent_expires_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=90)).isoformat(),
                "provider": {"provider_id": "ob-hsbc", "display_name": "HSBC"},
                "scopes": ["accounts", "balance", "cards", "transactions", "offline_access"],
            }])
        if path == "/accounts":
            return self._ok([{
                "account_id": f"acc-{g}", "account_type": "TRANSACTION", "display_name": "Advance",
                "currency": "GBP", "account_number": {"sort_code": "40-11-22", "number": "12345678"},
                "provider": {"provider_id": "ob-hsbc"},
            }])
        if path == f"/accounts/acc-{g}/balance":
            return self._ok([{"currency": "GBP", "current": 1234.56, "available": 1300.0}])
        if path == "/cards":
            return self._ok([{
                "account_id": f"card-{g}", "card_type": "CREDIT", "display_name": "HSBC Credit Card",
                "currency": "GBP", "partial_card_number": "5678", "provider": {"provider_id": "ob-hsbc"},
            }])
        if path == f"/cards/card-{g}/balance":
            return self._ok([{"currency": "GBP", "current": 250.0, "available": "2750", "credit_limit": 3000}])
        if path == f"/accounts/acc-{g}/transactions":
            return self._history(request, [
                ("rent", 3, -950.0, "DEBIT", "LANDLORD RENT"),
                ("salary", 10, 2500.0, "CREDIT", "ACME LTD SALARY"),
                ("old", 300, -12.0, "DEBIT", "TESCO STORES"),
                ("ancient", 500, -9.0, "DEBIT", "GREGGS"),
            ])
        if path == f"/cards/card-{g}/transactions":
            return self._history(request, [
                ("shop", 2, 24.25, "DEBIT", "SAINSBURYS SMRKT"),
                ("payment", 5, 100.0, "CREDIT", "PAYMENT RECEIVED"),
            ])
        return httpx.Response(404, json={"error": "account_not_found"})

    def _ok(self, results):
        return httpx.Response(200, json={"results": results, "status": "Succeeded"})

    def _token(self, form: dict) -> httpx.Response:
        assert form["client_id"] == ["cid"] and form["client_secret"] == ["csecret"]
        grant = form["grant_type"][0]
        if grant == "authorization_code":
            if form["code"][0] != "good-code":
                return httpx.Response(400, json={"error": "invalid_grant"})
            assert form["redirect_uri"] == ["https://console.truelayer.com/redirect-page"]
        elif grant == "refresh_token":
            if form["refresh_token"][0] not in self.refresh_tokens:
                return httpx.Response(400, json={"error": "invalid_grant"})
        token = f"rt-{len(self.refresh_tokens)}"
        self.refresh_tokens.add(token)
        return httpx.Response(200, json={"access_token": "at-1", "refresh_token": token, "expires_in": 3600, "token_type": "Bearer"})

    def _history(self, request: httpx.Request, rows) -> httpx.Response:
        start = dt.date.fromisoformat(request.url.params["from"])
        end = dt.date.fromisoformat(request.url.params["to"])
        if (end - start).days > self.history_days:
            return httpx.Response(403, json={"error": "sca_exceeded"})
        results = []
        for key, days_ago, amount, kind, description in rows:
            day = TODAY - dt.timedelta(days=days_ago)
            if start <= day <= end:
                results.append({
                    "transaction_id": f"{key}-{self.generation}",  # changes on reconnect
                    "normalised_provider_transaction_id": f"txn-{key}",  # doesn't
                    "timestamp": _day(days_ago), "description": description, "amount": amount,
                    "currency": "GBP", "transaction_type": kind, "transaction_classification": [],
                })
        return self._ok(results)

    def data_calls(self, fragment: str) -> list[tuple[str, str, dict]]:
        return [c for c in self.calls if fragment in c[1]]


@pytest.fixture
def fake(monkeypatch):
    fake = FakeTrueLayer()
    monkeypatch.setattr(settings, "truelayer_client_id", "cid")
    monkeypatch.setattr(settings, "truelayer_client_secret", "csecret")
    monkeypatch.setattr(truelayer, "_client", lambda: httpx.Client(transport=httpx.MockTransport(fake.handler)))
    return fake


@pytest.fixture
def hsbc(client, fake):
    """HSBC connected through the API, removed again afterwards."""
    response = client.post(
        "/api/truelayer/connect",
        json={"code": "https://console.truelayer.com/redirect-page?code=good-code&scope=accounts"},
        headers={"X-Forwarded-For": "81.2.69.160"},
    )
    assert response.status_code == 200, response.text
    yield response.json()
    db = SessionLocal()
    for connection in db.scalars(select(Connection).where(Connection.provider == "truelayer")):
        client.delete(f"/api/connections/{connection.id}", params={"forget_data": True})
    db.close()


def _rows(connection_id: int) -> dict[str, Transaction]:
    db = SessionLocal()
    try:
        accounts = db.scalars(select(Account).where(Account.connection_id == connection_id)).all()
        rows = db.scalars(select(Transaction).where(Transaction.account_id.in_([a.id for a in accounts]))).all()
        return {t.description: t for t in rows}
    finally:
        db.close()


# --- the auth link and the pasted code ------------------------------------------
def test_auth_link_asks_each_bank_only_for_what_it_offers(fake):
    url = truelayer.auth_link("ob-amex", ["info", "cards", "balance", "transactions", "offline_access"])
    query = parse_qs(urlparse(url).query)
    assert query["scope"] == ["cards balance transactions offline_access"]  # no info, no accounts
    assert query["provider_id"] == query["providers"] == ["ob-amex"]
    assert query["redirect_uri"] == ["https://console.truelayer.com/redirect-page"]
    assert "%20" in url and "scope=cards+" not in url  # spaces encoded the way TrueLayer documents


def test_pasted_code_or_whole_address_both_work():
    assert truelayer.parse_code("  abc123  ") == "abc123"
    assert truelayer.parse_code("https://console.truelayer.com/redirect-page?code=abc123&scope=x") == "abc123"
    with pytest.raises(ProviderError, match="access_denied"):
        truelayer.parse_code("https://console.truelayer.com/redirect-page?error=access_denied")
    with pytest.raises(ProviderError):
        truelayer.parse_code("")


def test_bank_list_hides_the_mock_bank_outside_sandbox(fake):
    assert [b["provider_id"] for b in truelayer.banks()] == ["ob-amex", "ob-hsbc"]


def test_only_public_addresses_are_passed_on_to_the_bank():
    def request(forwarded):
        return type("R", (), {"headers": {"x-forwarded-for": forwarded}, "client": None})()

    assert psu_ip(request("81.2.69.160, 172.30.32.2")) == "81.2.69.160"
    assert psu_ip(request("192.168.1.5")) is None
    assert psu_ip(request("")) is None


# --- connecting and reading -------------------------------------------------------
def test_connecting_reads_accounts_cards_balances_and_history(client, fake, hsbc):
    assert (hsbc["institution"], hsbc["accounts"]) == ("HSBC", 2)
    listed = next(c for c in client.get("/api/connections").json()["connections"] if c["provider"] == "truelayer")
    assert listed["bank_id"] == "ob-hsbc" and listed["status"] == "linked" and listed["expires_at"]
    assert "secret" not in listed and "rt-" not in str(listed)  # the token never reaches the browser

    rows = _rows(hsbc["connection_id"])
    assert rows["LANDLORD RENT"].amount_minor == -95000
    assert rows["ACME LTD SALARY"].amount_minor == 250000
    assert rows["SAINSBURYS SMRKT"].amount_minor == -2425  # a card purchase is money out
    assert rows["PAYMENT RECEIVED"].amount_minor == 10000
    # Two years refused, one year accepted: the 300-day-old row is in, the 500-day one isn't.
    assert "TESCO STORES" in rows and "GREGGS" not in rows

    db = SessionLocal()
    card = db.scalar(select(Account).where(Account.connection_id == hsbc["connection_id"], Account.kind == "credit_card"))
    current = db.scalar(select(Account).where(Account.connection_id == hsbc["connection_id"], Account.kind == "current"))
    db.close()
    assert card.balance_minor == -25000  # owed
    assert (current.balance_minor, current.available_minor) == (123456, 130000)
    assert "12345678" not in current.external_id  # the account number is hashed

    # Read while you were there: the bank was told, so its daily limit doesn't apply.
    assert all(h.get("x-psu-ip") == "81.2.69.160" for _, _, h in fake.data_calls("/data/v1/accounts"))


def test_connecting_the_same_bank_again_matches_what_is_there(client, fake, hsbc):
    before = _rows(hsbc["connection_id"])
    fake.generation += 1  # new consent: new account and transaction IDs from TrueLayer
    again = client.post("/api/truelayer/connect", json={"code": "good-code"})
    assert again.status_code == 200, again.text
    assert again.json()["connection_id"] == hsbc["connection_id"]
    assert again.json()["transactions_added"] == 0
    assert set(_rows(hsbc["connection_id"])) == set(before)
    db = SessionLocal()
    assert len(db.scalars(select(Connection).where(Connection.provider == "truelayer")).all()) == 1
    assert len(db.scalars(select(Account).where(Account.connection_id == hsbc["connection_id"])).all()) == 2
    db.close()


def test_a_used_or_late_code_explains_itself(client, fake):
    response = client.post("/api/truelayer/connect", json={"code": "stale-code"})
    assert response.status_code == 400
    assert "5 minutes" in response.json()["detail"]


def test_ended_access_asks_you_to_connect_again(client, fake, hsbc):
    db = SessionLocal()
    connection = db.get(Connection, hsbc["connection_id"])
    connection.secret = "revoked-token"
    db.commit()
    db.close()

    response = client.post(f"/api/connections/{hsbc['connection_id']}/sync")
    assert response.status_code == 502 and "Connect it again" in response.json()["detail"]
    listed = next(c for c in client.get("/api/connections").json()["connections"] if c["provider"] == "truelayer")
    assert listed["status"] == "expired"
    titles = [i["title"] for i in client.get("/api/insights").json()["insights"]]
    assert "HSBC needs connecting again" in titles

    # The schedule leaves an expired bank alone instead of failing every run.
    db = SessionLocal()
    result = sync_service.sync_all(db, unattended=True)
    db.close()
    assert not [r for r in result["results"] if r["provider"] == "truelayer"]


def test_schedule_reads_each_bank_at_most_every_eight_hours(fake, hsbc):
    fake.calls.clear()
    db = SessionLocal()
    sync_service.sync_all(db, unattended=True)
    assert not fake.data_calls("/data/v1/accounts")  # just read when it was connected

    connection = db.get(Connection, hsbc["connection_id"])
    connection.meta = {**connection.meta, "last_attempt_at": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=9)).isoformat()}
    db.commit()
    result = sync_service.sync_all(db, unattended=True)
    db.close()
    assert [r["institution"] for r in result["results"] if r["provider"] == "truelayer"] == ["HSBC"]
    assert fake.data_calls("/data/v1/accounts")
    assert not any("x-psu-ip" in h for _, _, h in fake.data_calls("/data/v1/"))  # nobody was there


def test_rotated_refresh_tokens_are_kept(client, fake, hsbc):
    db = SessionLocal()
    first = db.get(Connection, hsbc["connection_id"]).secret
    db.close()
    assert client.post(f"/api/connections/{hsbc['connection_id']}/sync").status_code == 200
    db = SessionLocal()
    second = db.get(Connection, hsbc["connection_id"]).secret
    db.close()
    assert second != first and second in fake.refresh_tokens


def test_disconnecting_tells_truelayer_to_drop_access(client, fake, hsbc):
    response = client.delete(f"/api/connections/{hsbc['connection_id']}", params={"forget_data": True})
    assert response.status_code == 200
    assert fake.deleted == 1
    assert not _rows(hsbc["connection_id"])
