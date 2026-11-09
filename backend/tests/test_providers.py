"""Trading 212 connector and statement imports."""

from __future__ import annotations

import base64
import datetime as dt

import httpx
import pytest

from app.config import settings
from app.providers import csv_import, starling, trading212
from app.providers.base import ProviderError, RawAccount

# --- Trading 212 ----------------------------------------------------------------
# Shaped like the AccountSummary and Position schemas in Trading 212's OpenAPI spec.
SUMMARY = {
    "id": 12345678,
    "currency": "GBP",
    "totalValue": 15234.56,
    "cash": {"availableToTrade": 120.5, "inPies": 0, "reservedForOrders": 0},
    "investments": {
        "currentValue": 15114.06,
        "totalCost": 12000.0,
        "unrealizedProfitLoss": 3114.06,
        "realizedProfitLoss": 250.0,
    },
}
POSITIONS = [
    {
        "instrument": {"ticker": "AAPL_US_EQ", "name": "Apple", "currency": "USD", "isin": "US0378331005"},
        "quantity": 10.0,
        "averagePricePaid": 150.0,
        "currentPrice": 230.0,
        "walletImpact": {
            "currency": "GBP",
            "currentValue": 1810.12,
            "totalCost": 1200.0,
            "unrealizedProfitLoss": 610.12,
        },
    },
    {
        "instrument": {"ticker": "VUAGl_EQ", "name": "Vanguard S&P 500 (Acc)", "currency": "GBP"},
        "quantity": 120.5,
        "averagePricePaid": 80.0,
        "currentPrice": 111.4,
        "walletImpact": {
            "currency": "GBP",
            "currentValue": 13303.94,
            "totalCost": 10800.0,
            "unrealizedProfitLoss": 2503.94,
        },
    },
]


@pytest.fixture
def t212_api(monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append((url, headers["Authorization"]))
        request = httpx.Request("GET", url)
        if headers["Authorization"] == "Basic bad":
            return httpx.Response(401, request=request)
        body = SUMMARY if url.endswith("/equity/account/summary") else POSITIONS
        return httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(trading212.httpx, "get", fake_get)
    return calls


def test_trading212_uses_key_and_secret_and_new_endpoints(t212_api, monkeypatch):
    monkeypatch.setattr(settings, "trading212_api_key", "KEY")
    monkeypatch.setattr(settings, "trading212_api_secret", "SECRET")
    [account] = trading212.fetch_accounts()

    expected = "Basic " + base64.b64encode(b"KEY:SECRET").decode()
    assert [(url.rsplit("/v0", 1)[1], auth) for url, auth in t212_api] == [
        ("/equity/account/summary", expected),
        ("/equity/positions", expected),
    ]
    assert account.external_id == "trading212-12345678"
    assert account.balance_minor == 1523456  # totalValue: cash + investments
    assert account.available_minor == 12050
    top = account.meta["holdings"][0]
    assert top["name"] == "Vanguard S&P 500 (Acc)"  # sorted by value
    apple = account.meta["holdings"][1]
    # Value and P/L in pounds from the API, the price left in dollars.
    assert (apple["value"], apple["ppl"], apple["current_price"], apple["price_currency"]) == (
        1810.12,
        610.12,
        230.0,
        "USD",
    )


def test_trading212_key_without_secret_uses_the_legacy_header(t212_api, monkeypatch):
    monkeypatch.setattr(settings, "trading212_api_key", "OLDKEY")
    monkeypatch.setattr(settings, "trading212_api_secret", "")
    trading212.fetch_accounts()
    assert {auth for _, auth in t212_api} == {"OLDKEY"}


def test_trading212_two_accounts(t212_api, monkeypatch):
    monkeypatch.setattr(settings, "trading212_api_key", "INVEST")
    monkeypatch.setattr(settings, "trading212_api_secret", "S1")
    monkeypatch.setattr(settings, "trading212_api_key_2", "ISA")
    monkeypatch.setattr(settings, "trading212_api_secret_2", "S2")
    assert settings.trading212_configured
    assert len(trading212.fetch_accounts()) == 2
    assert len(t212_api) == 4


def test_trading212_bad_credentials_explain_themselves(monkeypatch):
    monkeypatch.setattr(
        trading212.httpx,
        "get",
        lambda url, headers=None, timeout=None: httpx.Response(401, request=httpx.Request("GET", url)),
    )
    with pytest.raises(ProviderError, match="newer keys also need their API secret"):
        trading212.fetch_account(trading212.Credentials("KEY"))


# --- Starling ---------------------------------------------------------------------
class FakeStarling:
    """Answers transactions-between like Starling: one item per day, refusing
    windows longer than max_days or reaching back past `oldest`."""

    def __init__(self, max_days: int, oldest: dt.datetime):
        self.max_days, self.oldest, self.calls = max_days, oldest, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, path, params=None):
        lo = dt.datetime.fromisoformat(params["minTransactionTimestamp"].replace("Z", "+00:00"))
        hi = dt.datetime.fromisoformat(params["maxTransactionTimestamp"].replace("Z", "+00:00"))
        self.calls.append((lo, hi))
        request = httpx.Request("GET", "https://api.starlingbank.com" + path)
        if hi - lo > dt.timedelta(days=self.max_days) or lo < self.oldest:
            return httpx.Response(
                400, json={"errors": [{"message": "QUERY_EXCEEDING_MAX_TIME_RANGE"}], "success": False}, request=request
            )
        day = dt.datetime.combine(lo.date(), dt.time(12), tzinfo=dt.timezone.utc)
        items = []
        while day <= hi:  # edges are inclusive, so neighbouring windows overlap
            if day >= lo:
                items.append(
                    {
                        "feedItemUid": f"uid-{day.date()}",
                        "status": "SETTLED",
                        "direction": "OUT",
                        "amount": {"currency": "GBP", "minorUnits": 100},
                        "transactionTime": day.isoformat(),
                        "counterPartyName": "TESCO",
                    }
                )
            day += dt.timedelta(days=1)
        return httpx.Response(200, json={"feedItems": items}, request=request)


STARLING_ACCOUNT = RawAccount(
    external_id="acc-1",
    name="Personal",
    kind="current",
    currency="GBP",
    balance_minor=0,
    meta={"default_category": "cat-1"},
)


def test_starling_reads_two_years_in_windows_it_accepts(monkeypatch):
    now = dt.datetime.now(dt.timezone.utc)
    fake = FakeStarling(max_days=40, oldest=now - dt.timedelta(days=3650))
    monkeypatch.setattr(starling, "_client", lambda: fake)

    since = (now - dt.timedelta(days=730)).date()
    rows = starling.fetch_transactions(STARLING_ACCOUNT, since)

    assert len(fake.calls) < 40  # 90 and 45 days refused, then ~22-day windows
    assert len({r.external_id for r in rows}) == len(rows)  # overlapping edges not doubled
    assert min(r.booked_on for r in rows) == since
    assert max(r.booked_on for r in rows) >= now.date()


def test_starling_keeps_what_it_has_when_history_runs_out(monkeypatch):
    now = dt.datetime.now(dt.timezone.utc)
    fake = FakeStarling(max_days=400, oldest=now - dt.timedelta(days=200))
    monkeypatch.setattr(starling, "_client", lambda: fake)

    rows = starling.fetch_transactions(STARLING_ACCOUNT, (now - dt.timedelta(days=730)).date())

    assert rows and min(r.booked_on for r in rows) >= (now - dt.timedelta(days=200)).date()
    assert len(fake.calls) < 15


# --- statement imports ------------------------------------------------------------
def parse(text: str, **kwargs) -> csv_import.ParsedStatement:
    return csv_import.parse_statement(text.encode(), **kwargs)


def test_signed_amount_file_is_not_flipped():
    """A bank export with plain Date/Description/Amount must keep its signs."""
    statement = parse("Date,Description,Amount\n03/11/2026,TESCO STORES,-12.34\n04/11/2026,SALARY ACME,2500.00\n")
    assert statement.detected_format == "generic" and not statement.flipped
    assert [r.amount_minor for r in statement.rows] == [-1234, 250000]


def test_amex_export_is_recognised_and_flipped():
    text = (
        "Date,Description,Amount,Extended Details,Appears On Your Statement As,Category\n"
        "03/11/2026,NETFLIX.COM,10.99,Netflix,NETFLIX.COM,Entertainment\n"
        "05/11/2026,PAYMENT RECEIVED - THANK YOU,-300.00,,,\n"
    )
    statement = parse(text)
    assert statement.detected_format == "amex" and statement.flipped
    assert [r.amount_minor for r in statement.rows] == [-1099, 30000]


def test_headerless_three_column_file():
    statement = parse('03 Nov 2026,TESCO STORES 3315,-12.34\n04 Nov 2026,"ACME LTD SALARY","1,250.00"\n')
    assert statement.detected_format == "headerless"
    assert [(r.description, r.amount_minor) for r in statement.rows] == [
        ("TESCO STORES 3315", -1234),
        ("ACME LTD SALARY", 125000),
    ]


def test_headerless_file_with_a_balance_column_is_refused_clearly():
    with pytest.raises(ProviderError, match="Add a first line naming the columns"):
        parse("03/11/2026,TESCO,-12.34,987.66\n04/11/2026,SALARY,2500.00,3487.66\n")


def test_paid_in_paid_out_columns_and_pound_signs():
    text = (
        "Date,Description,Paid out,Paid in,Balance\n"
        "03/11/2026,TESCO,£12.34,,£987.66\n"
        '04/11/2026,SALARY,,"£2,500.00",£3487.66\n'
    )
    assert [r.amount_minor for r in parse(text).rows] == [-1234, 250000]


def test_bracketed_negative_amounts():
    assert [r.amount_minor for r in parse("Date,Description,Amount\n03/11/2026,TESCO,(12.34)\n").rows] == [-1234]


# --- the import endpoint ------------------------------------------------------------
def _account(client, name, kind):
    return client.post("/api/accounts/manual", data={"name": name, "kind": kind}).json()["id"]


def _import(client, account_id, text, **form):
    return client.post(
        "/api/import/csv",
        data={"account_id": str(account_id), **{k: str(v).lower() for k, v in form.items()}},
        files={"file": ("statement.csv", text, "text/csv")},
    ).json()


def test_card_statement_with_positive_purchases_is_flipped_for_card_accounts(client):
    card = _account(client, "Test card", "credit_card")
    text = (
        "Date,Description,Amount\n"
        "01/10/2026,SHOP A,25.00\n02/10/2026,SHOP B,40.00\n03/10/2026,PAYMENT THANK YOU,-65.00\n"
    )
    result = _import(client, card, text)
    assert result["signs_flipped"] is True
    assert (result["money_out"], result["money_in"]) == (65.0, 65.0)
    assert (result["first_date"], result["last_date"]) == ("2026-10-01", "2026-10-03")


def test_reimporting_with_flip_corrects_signs_instead_of_duplicating(client):
    current = _account(client, "Test current", "current")
    text = "Date,Description,Amount\n05/10/2026,GREGGS,4.50\n06/10/2026,PRET A MANGER,7.25\n"
    first = _import(client, current, text)
    assert first["added"] == 2 and first["money_in"] == 11.75  # wrong way round

    fixed = _import(client, current, text, flip_sign=True)
    assert fixed["added"] == 0 and fixed["duplicates_skipped"] == 2
    rows = client.get("/api/transactions", params={"account_id": current}).json()
    items = rows["transactions"] if isinstance(rows, dict) else rows
    mine = [t for t in items if t["merchant"].lower().startswith(("greggs", "pret"))]
    assert sorted(t["amount"] for t in mine) == [-7.25, -4.5]
    assert {t["category"] for t in mine} == {"Eating Out"}  # re-categorised as spending


# --- Starling limits ---------------------------------------------------------------
class _Limited:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, path, params=None):
        return httpx.Response(429, text="", request=httpx.Request("GET", "https://api.starlingbank.com" + path))


def test_starling_rate_limit_waits_for_the_next_sync(fresh_db, monkeypatch):
    from app.models import Connection
    from app.services import sync as sync_service

    monkeypatch.setattr(starling, "_client", lambda: _Limited())
    result = sync_service.sync_starling(fresh_db)

    assert "error" not in result and "limiting requests" in result["skipped"]
    connection = fresh_db.query(Connection).filter_by(provider="starling").one()
    assert connection.status == "linked"  # the token works; it's only throttled
    assert "…" not in connection.last_error and "/api/" not in connection.last_error


def test_starling_errors_leave_out_account_ids():
    class _Broken(_Limited):
        def get(self, path, params=None):
            return httpx.Response(500, text="oops", request=httpx.Request("GET", "https://x" + path))

    path = "/api/v2/feed/account/f6dac7c5-0000-4000-8000-000000000000/category/a1feb10b-0000-4000-8000-000000000000"
    with pytest.raises(ProviderError) as err:
        starling._get(_Broken(), path)
    assert "f6dac7c5" not in str(err.value)
