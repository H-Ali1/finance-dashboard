"""Syncing reads only what's new, and the same transaction is never stored
twice: not when a bank sends it again, not under a new ID after connecting a
bank again, and not as a pending copy of a payment the bank cancelled."""

from __future__ import annotations

import datetime as dt

import httpx
from sqlalchemy import func, select

from app.analytics import spending
from app.models import Account, Transaction
from app.providers import starling
from app.providers.base import RawAccount, RawTransaction
from app.services import sync as sync_service

TODAY = dt.date.today()


def _account(db, provider="truelayer") -> Account:
    account = Account(provider=provider, external_id=f"{provider}-1", name="Current", provider_meta={})
    db.add(account)
    db.commit()
    return account


def _raw(uid, days_ago, pence, description="EXAMPLE CAFE", status="booked"):
    return RawTransaction(
        external_id=uid,
        booked_on=TODAY - dt.timedelta(days=days_ago),
        amount_minor=pence,
        description=description,
        status=status,
    )


def _count(db) -> int:
    return db.scalar(select(func.count()).select_from(Transaction))


def test_the_next_read_starts_from_what_is_already_stored(fresh_db):
    account = _account(fresh_db)
    assert sync_service._since_for(fresh_db, account) == TODAY - dt.timedelta(days=730)  # nothing yet
    sync_service.upsert_transactions(fresh_db, account, [_raw("a", 30, -500), _raw("b", 2, -700)])
    # Only new data, plus a few days for payments that settle late.
    assert sync_service._since_for(fresh_db, account) == TODAY - dt.timedelta(days=2 + 7)


def test_an_old_pending_payment_is_read_again_until_it_settles(fresh_db):
    account = _account(fresh_db)
    sync_service.upsert_transactions(fresh_db, account, [_raw("held", 20, -6000, status="pending"), _raw("b", 1, -700)])
    assert sync_service._since_for(fresh_db, account) == TODAY - dt.timedelta(days=20)


def test_the_same_batch_twice_adds_nothing(fresh_db):
    account = _account(fresh_db)
    batch = [_raw("a", 3, -320), _raw("b", 3, -320), _raw("c", 1, -1250, "EXAMPLE SHOP")]
    assert sync_service.upsert_transactions(fresh_db, account, batch) == 3
    assert sync_service.upsert_transactions(fresh_db, account, batch) == 0
    assert _count(fresh_db) == 3
    total = spending.category_breakdown(fresh_db, TODAY - dt.timedelta(days=10), TODAY)["total_spend"]
    assert total == 18.90  # two coffees and a purchase, each counted once


def test_new_ids_after_connecting_again_update_the_stored_rows(fresh_db):
    """Some banks have no stable transaction ID, so TrueLayer's changes when
    the bank is connected again. Two identical coffees stay two."""
    account = _account(fresh_db)
    sync_service.upsert_transactions(
        fresh_db, account, [_raw("old-1", 3, -320), _raw("old-2", 3, -320), _raw("old-3", 1, -1250, "EXAMPLE SHOP")]
    )
    added = sync_service.upsert_transactions(
        fresh_db,
        account,
        [
            _raw("new-1", 3, -320),
            _raw("new-2", 3, -320),
            _raw("new-3", 1, -1250, "EXAMPLE SHOP"),
            _raw("new-4", 0, -99),
        ],
    )
    assert added == 1  # only the genuinely new one
    ids = set(fresh_db.scalars(select(Transaction.external_id)))
    assert ids == {"new-1", "new-2", "new-3", "new-4"}


def test_a_cancelled_payment_removes_its_pending_copy(fresh_db, monkeypatch):
    account = _account(fresh_db, provider="starling")
    item = {
        "feedItemUid": "hold-1",
        "amount": {"currency": "GBP", "minorUnits": 4500},
        "direction": "OUT",
        "transactionTime": f"{(TODAY - dt.timedelta(days=2)).isoformat()}T09:00:00.000Z",
        "source": "MASTER_CARD",
        "counterPartyName": "EXAMPLE HOTEL",
        "spendingCategory": "HOLIDAYS",
    }

    class Feed:
        def __init__(self, status):
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, path, params=None):
            body = {"feedItems": [{**item, "status": self.status}]}
            return httpx.Response(200, json=body, request=httpx.Request("GET", "https://api.starlingbank.com" + path))

    raw_account = RawAccount(external_id="acc-1", name="Personal", meta={"default_category": "cat-1"})
    since = TODAY - dt.timedelta(days=10)
    monkeypatch.setattr(starling, "_client", lambda: Feed("PENDING"))
    sync_service.upsert_transactions(fresh_db, account, starling.fetch_transactions(raw_account, since))
    assert _count(fresh_db) == 1

    monkeypatch.setattr(starling, "_client", lambda: Feed("REVERSED"))
    sync_service.upsert_transactions(fresh_db, account, starling.fetch_transactions(raw_account, since))
    assert _count(fresh_db) == 0  # no phantom charge left behind


def test_one_bank_imported_and_connected_is_flagged_and_can_be_left_out(fresh_db):
    """The same account as a statement import and a live feed: the insight
    names both, and unticking one takes its spending out of every total."""
    from app.analytics import anomalies

    statement = Account(provider="csv", external_id="manual:current", name="Statement import", provider_meta={})
    live = Account(provider="truelayer", external_id="tl-1", name="Live feed", provider_meta={})
    fresh_db.add_all([statement, live])
    fresh_db.commit()
    rows = [(n, -(500 + n * 37)) for n in range(1, 13)]
    sync_service.upsert_transactions(fresh_db, statement, [_raw(f"s{n}", n, p, f"SHOP {n}") for n, p in rows])
    # The feed dates some a day later and words them differently.
    sync_service.upsert_transactions(fresh_db, live, [_raw(f"l{n}", n - 1, p, f"Shop {n} Ltd") for n, p in rows])

    found = anomalies.detect_duplicate_accounts(fresh_db)
    assert [(d["accounts"], d["shared"]) for d in found] == [(["Statement import", "Live feed"], 12)]

    window = (TODAY - dt.timedelta(days=30), TODAY)
    both = spending.category_breakdown(fresh_db, *window)["total_spend"]
    statement.is_active = False  # what unticking "Counted" does
    fresh_db.commit()
    assert spending.category_breakdown(fresh_db, *window)["total_spend"] == round(both / 2, 2)
    assert anomalies.detect_duplicate_accounts(fresh_db) == []


def test_a_left_out_account_stays_out_after_syncing(fresh_db):
    live = Account(provider="truelayer", external_id="tl-1", name="Old card", provider_meta={"hidden": True})
    fresh_db.add(live)
    fresh_db.commit()
    sync_service.upsert_account(fresh_db, "truelayer", RawAccount(external_id="tl-1", name="Old card"), None)
    assert live.is_active is False


def test_one_request_works_each_result_out_once(fresh_db, monkeypatch):
    from app.analytics import cashflow, insights, recurring

    calls = {"n": 0}
    real = recurring.load_transactions

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(recurring, "load_transactions", counting)
    insights.headline(fresh_db)  # cash flow, insights and anomalies all want recurring payments
    insights.build_insights(fresh_db)
    cashflow.cashflow(fresh_db)
    assert calls["n"] == 1

    # A copy each time: changing one answer doesn't change the next.
    first = cashflow.cashflow(fresh_db)
    first.pop("_minor")
    assert "_minor" in cashflow.cashflow(fresh_db)

    # Anything saved starts afresh.
    fresh_db.commit()
    recurring.detect_recurring(fresh_db)
    assert calls["n"] == 2


def test_old_sync_history_is_pruned(fresh_db):
    from app.models import SyncLog

    fresh_db.add_all(
        [
            SyncLog(provider="starling", started_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=200)),
            SyncLog(provider="starling", started_at=dt.datetime.now(dt.timezone.utc)),
        ]
    )
    fresh_db.commit()
    assert sync_service.prune_sync_logs(fresh_db) == 1
    assert fresh_db.scalar(select(func.count()).select_from(SyncLog)) == 1


def test_the_same_rule_saved_twice_is_one_rule(client):
    from app.db import SessionLocal
    from app.models import CategoryRule

    body = {"pattern": "Example Gym", "category": "Health & Fitness", "apply_now": False}
    first = client.post("/api/rules", json=body).json()["id"]
    again = client.post("/api/rules", json={**body, "category": "Entertainment"}).json()["id"]
    db = SessionLocal()
    try:
        rules = db.scalars(select(CategoryRule).where(CategoryRule.pattern == "example gym")).all()
        assert first == again and [r.category for r in rules] == ["Entertainment"]
        db.delete(rules[0])
        db.commit()
    finally:
        db.close()


def test_a_cancellation_never_removes_a_booked_payment(fresh_db):
    account = _account(fresh_db)
    sync_service.upsert_transactions(fresh_db, account, [_raw("paid", 1, -4500, "EXAMPLE HOTEL")])
    # A cancellation of a different hold, with the same date, amount and wording.
    sync_service.upsert_transactions(fresh_db, account, [_raw("hold", 1, -4500, "EXAMPLE HOTEL", status="reversed")])
    assert _count(fresh_db) == 1


def test_savings_rate_limit_is_a_wait_not_missing_savings(monkeypatch):
    import pytest

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, path, params=None):
            req = httpx.Request("GET", "https://api.starlingbank.com" + path)
            if path.endswith("/accounts"):
                return httpx.Response(
                    200, json={"accounts": [{"accountUid": "a", "defaultCategory": "c"}]}, request=req
                )
            if path.endswith("/balance"):
                return httpx.Response(200, json={}, request=req)
            return httpx.Response(429, text="", request=req)

    monkeypatch.setattr(starling, "_client", lambda: Client())
    with pytest.raises(starling.RateLimited):
        starling.fetch_accounts()


def test_one_rule_per_pattern(tmp_path):
    """Copies saved by older versions are folded into the latest, and the
    database refuses new copies."""
    import sqlite3

    import pytest
    from sqlalchemy import create_engine
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import sessionmaker

    from app import migrations
    from app.models import CategoryRule
    from app.routers.transactions import create_rule
    from app.schemas import RuleCreate

    path = tmp_path / "rules.db"
    raw = sqlite3.connect(path)  # as an older version left it: no constraint, two copies
    raw.execute(
        "CREATE TABLE category_rules (id INTEGER PRIMARY KEY, pattern VARCHAR(160), match_type VARCHAR(16), "
        "field VARCHAR(16), category VARCHAR(48), priority INTEGER, created_at DATETIME)"
    )
    raw.executemany(
        "INSERT INTO category_rules (pattern, match_type, field, category, priority, created_at) "
        "VALUES ('example gym', 'contains', 'merchant', ?, ?, '2026-01-01')",
        [("Shopping", 50), ("Gifts", 100)],
    )
    raw.execute("PRAGMA user_version = 4")
    raw.commit()
    raw.close()

    engine = create_engine(f"sqlite:///{path.as_posix()}")
    migrations.migrate(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    assert [(r.category, r.priority) for r in db.scalars(select(CategoryRule))] == [("Gifts", 100)]

    create_rule(RuleCreate(pattern="Example Gym", category="Health & Fitness", apply_now=False), db=db)
    assert [r.category for r in db.scalars(select(CategoryRule))] == ["Health & Fitness"]
    db.add(CategoryRule(pattern="example gym", match_type="contains", field="merchant", category="Shopping"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.close()
    engine.dispose()


def test_a_category_chosen_for_a_merchant_beats_broader_rules(fresh_db):
    from app.models import CategoryRule
    from app.routers.transactions import categorise_merchant
    from app.schemas import MerchantCategory
    from app.services.categorise import categorise

    fresh_db.add(CategoryRule(pattern="pret", match_type="contains", field="merchant", category="Shopping"))
    fresh_db.commit()
    categorise_merchant(MerchantCategory(merchant="Pret A Manger", category="Eating Out"), db=fresh_db)
    rules = sync_service._rules(fresh_db)
    assert categorise(merchant="PRET A MANGER", description="", amount_minor=-450, rules=rules)[0] == "Eating Out"
    # The broader rule still covers other merchants.
    assert categorise(merchant="INTERPRET LTD", description="", amount_minor=-450, rules=rules)[0] == "Shopping"

    # And a more specific rule keeps its place over a broader merchant choice.
    fresh_db.add(
        CategoryRule(pattern="amazon prime", match_type="contains", field="merchant", category="Subscriptions")
    )
    fresh_db.commit()
    categorise_merchant(MerchantCategory(merchant="Amazon", category="Shopping"), db=fresh_db)
    rules = sync_service._rules(fresh_db)
    assert categorise(merchant="AMAZON PRIME", description="", amount_minor=-899, rules=rules)[0] == "Subscriptions"
    assert categorise(merchant="AMAZON", description="", amount_minor=-2000, rules=rules)[0] == "Shopping"
