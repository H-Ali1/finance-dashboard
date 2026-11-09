"""Categorising: keywords, the banks' own categories and payment types, the
one-off read that fills in payment types, and categorising a merchant from
the UI. Rows are shaped like what HSBC (through TrueLayer) and Starling send.
"""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, CategoryRule, Transaction
from app.providers import starling, truelayer
from app.providers.base import RawAccount, RawTransaction
from app.services import sync as sync_service
from app.services.categorise import categorise

TODAY = dt.date.today()


def _category(description: str, amount: float = -12.5, **kwargs) -> str:
    return categorise(merchant=description, description=description, amount_minor=round(amount * 100), **kwargs)[0]


# --- keywords -----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("description", "expected"),
    [
        # whole words only: these used to match "bar", "rent", "plum", "chip", "tui", "bus"
        ("BARCLAYCARD", "Uncategorised"),
        ("THE BAR SOHO", "Eating Out"),
        ("TRANSFER TO CURRENT ACCOUNT", "Transfers"),
        ("PARENTPAY", "Uncategorised"),
        ("J SMITH PLUMBER", "Uncategorised"),
        ("FISH AND CHIPS CO", "Eating Out"),
        ("MATHS TUITION", "Education"),
        ("BUSINESS DESIGN CENTRE", "Uncategorised"),
        # plurals and possessives
        ("SAINSBURYS S/MKT", "Groceries"),
        ("MCDONALDS 1234", "Eating Out"),
        ("NANDO'S CHICKENLAND", "Eating Out"),
        ("M&S SIMPLY FOOD", "Groceries"),
        # the brand at the start beats a place name later on
        ("TESCO STORES 3315 HEATHROW AIRPORT", "Groceries"),
        # the longer keyword wins where two start together
        ("AMAZON PRIME MEMBERSHIP", "Subscriptions"),
        ("AMAZON.CO.UK*MK4TR", "Shopping"),
        ("SHELL ENERGY RETAIL", "Bills & Utilities"),
        ("SHELL WEMBLEY", "Fuel"),
        ("ENTERPRISE RENT-A-CAR", "Travel"),
        ("UBER *EATS", "Eating Out"),
        ("UBER *TRIP", "Transport"),
        # rent to a housing association, as a bank shows a standing order
        ("EXAMPLE HOMES", "Rent & Mortgage"),
        ("HMRC SELF ASSESSMENT", "Tax"),
    ],
)
def test_keywords(description, expected):
    assert _category(description) == expected


def test_income_words_only_count_for_money_in():
    assert _category("HMRC SELF ASSESSMENT", amount=-850) == "Tax"
    assert _category("HMRC TAX REFUND", amount=212.4) == "Income"
    assert _category("ACME LTD SALARY", amount=2450) == "Income"


# --- the banks' own categories ------------------------------------------------------
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("RENT", "Rent & Mortgage"),  # set in the Starling app: used to be lost
        ("COFFEE", "Eating Out"),
        ("FUEL", "Fuel"),
        ("SUBSCRIPTIONS", "Subscriptions"),
        ("HOLIDAYS", "Travel"),
        ("GENERAL", "Uncategorised"),
        ("Fees & Interest", "Fees & Interest"),  # TrueLayer's, already in our terms
    ],
)
def test_provider_categories(code, expected):
    assert _category("SOMEONE NEW", provider_category=code) == expected


@pytest.mark.parametrize(
    ("row", "payment_type", "category"),
    [
        (
            {  # a standing order: TrueLayer says what it is, but gives no classification
                "description": "EXAMPLE HOMES",
                "amount": -850.0,
                "transaction_type": "DEBIT",
                "transaction_category": "STANDING_ORDER",
                "transaction_classification": [],
                "meta": {"provider_transaction_category": "SO", "provider_reference": "TENANCY 0001"},
            },
            "standing_order",
            "Rent & Mortgage",
        ),
        (
            {  # TrueLayer only says "debit"; HSBC's own code says direct debit
                "description": "TV LICENCE MBP",
                "amount": -14.54,
                "transaction_type": "DEBIT",
                "transaction_category": "DEBIT",
                "transaction_classification": [],
                "meta": {"provider_transaction_category": "DD"},
            },
            "direct_debit",
            "Bills & Utilities",
        ),
        (
            {  # a card purchase TrueLayer has classified
                "description": "MFG ACTON VALE",
                "merchant_name": "MFG",
                "amount": -61.2,
                "transaction_type": "DEBIT",
                "transaction_category": "PURCHASE",
                "transaction_classification": ["Auto & Transport", "Gas & Fuel"],
                "meta": {"provider_transaction_category": "VIS"},
            },
            "card",
            "Fuel",
        ),
        (
            {
                "description": "NON-STERLING TRANSACTION FEE",
                "amount": -1.5,
                "transaction_type": "DEBIT",
                "transaction_category": "FEE_CHARGE",
                "transaction_classification": ["Fees & Charges", "Bank Fee"],
                "meta": {},
            },
            "fee",
            "Fees & Interest",
        ),
        (
            {  # cash from a machine inside a supermarket is cash, not groceries
                "description": "LNK SAINSBURYS ACTON",
                "amount": -40.0,
                "transaction_type": "DEBIT",
                "transaction_category": "ATM",
                "transaction_classification": [],
                "meta": {"provider_transaction_category": "ATM"},
            },
            "cash",
            "Cash",
        ),
    ],
)
def test_truelayer_rows(row, payment_type, category):
    raw = truelayer._raw_transaction(
        {"transaction_id": "t1", "timestamp": "2026-11-02T00:00:00Z", "currency": "GBP", **row}, is_card=False
    )
    assert raw.payment_type == payment_type
    got, _ = categorise(
        merchant=raw.merchant,
        description=raw.description,
        amount_minor=raw.amount_minor,
        provider_category=raw.provider_category,
        payment_type=raw.payment_type,
    )
    assert got == category


class _Feed:
    def __init__(self, items):
        self.items = items

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, path, params=None):
        return httpx.Response(
            200, json={"feedItems": self.items}, request=httpx.Request("GET", "https://api.starlingbank.com" + path)
        )


def _starling_item(uid, direction, pence, counterparty, source, category, sub_type=None, reference=""):
    item = {
        "feedItemUid": uid,
        "categoryUid": "cat-1",
        "amount": {"currency": "GBP", "minorUnits": pence},
        "direction": direction,
        "transactionTime": f"{(TODAY - dt.timedelta(days=3)).isoformat()}T06:12:44.123Z",
        "source": source,
        "status": "SETTLED",
        "counterPartyType": "PAYEE" if direction == "OUT" else "SENDER",
        "counterPartyName": counterparty,
        "reference": reference,
        "spendingCategory": category,
    }
    if sub_type:
        item["sourceSubType"] = sub_type
    return item


def test_starling_payment_types(monkeypatch):
    feed = _Feed(
        [
            _starling_item("a", "OUT", 4000, "LINK ATM", "MASTER_CARD", "CASH", sub_type="ATM"),
            _starling_item("b", "OUT", 1299, "Netflix", "MASTER_CARD", "SUBSCRIPTIONS", sub_type="CONTACTLESS"),
            _starling_item("c", "OUT", 5000, "Instant Saver", "INTERNAL_TRANSFER", "SAVING"),
            _starling_item("d", "IN", 245000, "ACME LTD", "DIRECT_CREDIT", "INCOME", reference="SALARY"),
            _starling_item("e", "OUT", 3500, "EE Limited", "DIRECT_DEBIT", "BILLS_AND_SERVICES"),
        ]
    )
    monkeypatch.setattr(starling, "_client", lambda: feed)
    account = RawAccount(external_id="acc-1", name="Personal", meta={"default_category": "cat-1"})
    rows = {r.external_id: r for r in starling.fetch_transactions(account, TODAY - dt.timedelta(days=10))}
    assert {uid: rows[uid].payment_type for uid in rows} == {
        "a": "cash",
        "b": "card",
        "c": "internal",
        "d": "transfer",
        "e": "direct_debit",
    }


# --- rows saved before payment types were kept --------------------------------------
def _account(db, provider="truelayer", **meta) -> Account:
    account = Account(provider=provider, external_id=f"{provider}-1", name="Advance", provider_meta=meta)
    db.add(account)
    db.commit()
    return account


def test_older_rows_get_their_payment_type_when_the_bank_sends_them_again(fresh_db):
    account = _account(fresh_db)
    old = RawTransaction(
        external_id="tl:txn-atm", booked_on=TODAY - dt.timedelta(days=40), amount_minor=-4000, description="LNK ACTON"
    )
    sync_service.upsert_transactions(fresh_db, account, [old])
    row = fresh_db.scalar(select(Transaction))
    assert (row.payment_type, row.category) == (None, "Uncategorised")

    # Not read since the upgrade: the next read reaches back as far as TrueLayer allows.
    assert sync_service._since_for(fresh_db, account) == TODAY - dt.timedelta(days=89)

    again = RawTransaction(
        external_id="tl:txn-atm",
        booked_on=old.booked_on,
        amount_minor=-4000,
        description="LNK ACTON",
        payment_type="cash",
    )
    assert sync_service.upsert_transactions(fresh_db, account, [again]) == 0  # updated, not added
    sync_service._mark_read(fresh_db, account)
    fresh_db.refresh(row)
    assert (row.payment_type, row.category) == ("cash", "Cash")
    # Once read, back to the usual few days of overlap.
    assert sync_service._since_for(fresh_db, account) == old.booked_on - dt.timedelta(days=7)


def test_manual_categories_survive_the_extra_detail(fresh_db):
    account = _account(fresh_db, payment_types=True)
    raw = RawTransaction(external_id="x", booked_on=TODAY, amount_minor=-4000, description="LNK ACTON")
    sync_service.upsert_transactions(fresh_db, account, [raw])
    row = fresh_db.scalar(select(Transaction))
    row.category, row.category_source = "Gifts & Charity", "manual"
    fresh_db.commit()

    raw.payment_type = "cash"
    sync_service.upsert_transactions(fresh_db, account, [raw])
    fresh_db.refresh(row)
    assert (row.payment_type, row.category) == ("cash", "Gifts & Charity")


def test_a_longer_rule_beats_a_shorter_one_at_the_same_priority(fresh_db):
    fresh_db.add_all(
        [
            CategoryRule(pattern="amazon", category="Shopping"),
            CategoryRule(pattern="amazon prime", category="Subscriptions"),
        ]
    )
    fresh_db.commit()
    rules = sync_service._rules(fresh_db)
    assert categorise(merchant="AMAZON PRIME", description="", amount_minor=-899, rules=rules)[0] == "Subscriptions"
    assert categorise(merchant="AMAZON MKTPLACE", description="", amount_minor=-899, rules=rules)[0] == "Shopping"


# --- categorising a merchant from the UI --------------------------------------------
def test_categorising_a_merchant_covers_past_and_future_payments(client):
    db = SessionLocal()
    account = Account(provider="csv", external_id="manual:quick-categorise", name="Quick categorise test")
    db.add(account)
    db.commit()
    past = [
        RawTransaction(
            external_id=f"qc-{n}",
            booked_on=TODAY - dt.timedelta(days=30 * n + 2),
            amount_minor=-4500,
            description="FAITH WINDOW CLEANING 07700900123",
        )
        for n in range(3)
    ]
    sync_service.upsert_transactions(db, account, past)
    try:
        listed = client.get("/api/categorise/uncategorised", params={"days": 120, "limit": 500}).json()
        mine = next(m for m in listed["merchants"] if m["merchant_key"] == "faith window cleaning")
        assert (mine["transactions"], mine["total"]) == (3, 135.0)

        saved = client.post(
            "/api/categorise/merchant", json={"merchant": mine["merchant"], "category": "Bills & Utilities"}
        ).json()
        assert saved["recategorised"] >= 3
        # Saving it again changes the rule rather than adding a second one.
        client.post("/api/categorise/merchant", json={"merchant": mine["merchant"], "category": "Bills & Utilities"})
        assert len(db.scalars(select(CategoryRule).where(CategoryRule.pattern == "faith window cleaning")).all()) == 1

        future = RawTransaction(
            external_id="qc-next", booked_on=TODAY, amount_minor=-4500, description="FAITH WINDOW CLEANING 07700900123"
        )
        sync_service.upsert_transactions(db, account, [future])
        db.expire_all()
        rows = db.scalars(select(Transaction).where(Transaction.account_id == account.id)).all()
        assert {(r.category, r.category_source) for r in rows} == {("Bills & Utilities", "rule")}

        listed = client.get("/api/categorise/uncategorised", params={"days": 120, "limit": 500}).json()
        assert all(m["merchant_key"] != "faith window cleaning" for m in listed["merchants"])
    finally:
        for rule in db.scalars(select(CategoryRule).where(CategoryRule.pattern == "faith window cleaning")):
            db.delete(rule)
        db.delete(db.get(Account, account.id))
        db.commit()
        db.close()


def test_a_merchant_rule_matches_whole_words_only():
    rule = CategoryRule(pattern="pret", match_type="words", field="merchant", category="Eating Out")
    assert categorise(merchant="PRET A MANGER", description="", amount_minor=-450, rules=[rule])[1] == "rule"
    assert categorise(merchant="PRETTY THINGS", description="", amount_minor=-450, rules=[rule])[1] == "auto"
    assert categorise(merchant="INTERPRET LTD", description="", amount_minor=-450, rules=[rule])[1] == "auto"
    renamed = CategoryRule(pattern="example homes", match_type="words", field="merchant", category="Rent & Mortgage")
    assert (
        categorise(merchant="EXAMPLE HOMES FLAT 2", description="", amount_minor=-85000, rules=[renamed])[1] == "rule"
    )


def test_a_rule_on_a_name_in_another_script_matches():
    """Such a name normalises to nothing, so the rule matches it as written."""
    rule = CategoryRule(pattern="商店", match_type="words", field="merchant", category="Groceries")
    assert categorise(merchant="商店", description="商店", amount_minor=-1200, rules=[rule]) == ("Groceries", "rule")


def test_regex_rules_keep_their_case(client):
    response = client.post(
        "/api/rules",
        json={"pattern": r"^\S+ TUTOR", "category": "Income", "match_type": "regex", "apply_now": False},
    )
    db = SessionLocal()
    try:
        rule = db.get(CategoryRule, response.json()["id"])
        assert rule.pattern == r"^\S+ TUTOR"
    finally:
        db.delete(rule)
        db.commit()
        db.close()
