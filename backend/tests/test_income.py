"""Income: salary and tutoring apart, month by month; refunds and your own
money never counted as income. Rows are shaped like what the banks send:
a BACS salary into Starling, students paying by Faster Payments into HSBC
(through TrueLayer), card refunds on Amex and Starling.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from app.analytics import income as income_mod
from app.analytics import spending
from app.models import Account, CategoryRule, Transaction
from app.providers.base import RawTransaction
from app.routers.transactions import categorise_merchant
from app.schemas import MerchantCategory
from app.services import income as income_service
from app.services import sync as sync_service
from app.services.categorise import categorise
from app.services.transfers import NOT_A_TRANSFER, detect_transfers

TODAY = dt.date.today()


def _month(months_back: int, day: int) -> dt.date:
    year, month = TODAY.year, TODAY.month - months_back
    while month < 1:
        year, month = year - 1, month + 12
    return dt.date(year, month, day)


def _raw(uid, day, pounds, description, payment_type=None, provider_category=None):
    return RawTransaction(
        external_id=uid,
        booked_on=day,
        amount_minor=round(pounds * 100),
        description=description,
        merchant=description,
        payment_type=payment_type,
        provider_category=provider_category,
    )


@pytest.fixture
def banks(fresh_db):
    db = fresh_db
    starling = Account(provider="starling", external_id="st", name="Starling Personal", kind="current")
    hsbc = Account(
        provider="truelayer",
        external_id="hsbc",
        name="HSBC Advance",
        kind="current",
        provider_meta={"income_default": "Tutoring"},  # "my students pay into HSBC"
    )
    amex = Account(provider="truelayer", external_id="amex", name="Amex Gold", kind="credit_card")
    db.add_all([starling, hsbc, amex])
    db.commit()

    starling_rows, hsbc_rows, amex_rows = [], [], []
    for n in range(1, 7):  # six complete months
        # BACS from the employer: no "salary" anywhere in it.
        starling_rows.append(_raw(f"sal-{n}", _month(n, 25), 2450.00, "ACME SOFTWARE LTD", "transfer", "INCOME"))
        # Students, by Faster Payments into HSBC.
        hsbc_rows += [
            _raw(f"pat-{n}", _month(n, 3), 35.00, "MRS A PATEL AYAAN MATHS", "transfer"),
            _raw(f"oka-{n}", _month(n, 10), 40.00, "J OKAFOR LESSON", "transfer"),
            _raw(f"wri-{n}", _month(n, 17), 30.00, "S WRIGHT", "transfer"),  # no reference at all
        ]
        # Moving money from Starling to HSBC, by your own name.
        starling_rows.append(_raw(f"out-{n}", _month(n, 26), -200.00, "Sam Doe", "transfer", "PAYMENTS"))
        hsbc_rows.append(_raw(f"in-{n}", _month(n, 26), 200.00, "SAM DOE", "transfer"))

    # This month: a refund on each card, and money from an account that isn't connected.
    this = TODAY.replace(day=1)
    amex_rows += [
        _raw("buy", this, -100.00, "AMAZON.CO.UK*RF1AB", "card"),
        _raw("ref", this, 24.99, "AMAZON.CO.UK*RF1AB"),  # TrueLayer: a credit on a card account
    ]
    starling_rows += [
        _raw("asos", this, 45.00, "ASOS", "card", "SHOPPING"),
        _raw("int", this, 1.23, "INTEREST EARNED", "interest"),
        _raw("chase", this, 150.00, "SAM DOE", "transfer"),  # from Chase: not connected here
    ]
    hsbc_rows += [_raw("pat-now", this, 35.00, "MRS A PATEL AYAAN MATHS", "transfer")]

    for account, rows in ((starling, starling_rows), (hsbc, hsbc_rows), (amex, amex_rows)):
        sync_service.upsert_transactions(db, account, rows)
    detect_transfers(db)
    sync_service.recategorise_all(db)
    return db


def _categories(db) -> dict[str, str]:
    return {t.external_id: t.category for t in db.scalars(select(Transaction))}


def test_each_payment_lands_in_the_right_place(banks):
    got = _categories(banks)
    assert {got[f"sal-{n}"] for n in range(1, 7)} == {"Salary"}  # an organisation, every month
    assert {got[f"{p}-{n}"] for p in ("pat", "oka", "wri") for n in range(1, 7)} == {"Tutoring"}
    assert got["ref"] == "Shopping" and got["asos"] == "Shopping"  # refunds, not income
    assert got["int"] == "Income"
    assert got["chase"] == "Transfers"  # your own name, seen on transfers the app matched


def test_refunds_reduce_spending_instead_of_counting_as_income(banks):
    month = TODAY.strftime("%Y-%m")
    shopping = next(c for c in spending.category_breakdown(banks, TODAY.replace(day=1), TODAY)["categories"])
    assert shopping["category"] == "Shopping" and shopping["total"] == round(100 - 24.99 - 45.00, 2)
    trend = {m["month"]: m for m in spending.monthly_trend(banks, 2)["months"]}
    assert trend[month]["income"] == 35.00 + 1.23  # tutoring and interest; no refunds, no own money


def test_income_by_source(banks):
    data = income_mod.income_breakdown(banks, months=7)
    salary, tutoring, other = data["sources"]
    assert (salary["label"], salary["typical"], salary["regularity"], salary["payday"]) == (
        "Salary",
        2450.0,
        "Every month",
        25,
    )
    assert salary["trend"]["direction"] == "steady"
    assert (tutoring["typical"], tutoring["payers_per_month"], tutoring["payments_per_month"]) == (105.0, 3, 3)
    assert tutoring["this_month"] == 35.0 and tutoring["regularity"] == "Every month"
    assert other["this_month"] == 1.23
    assert data["not_income"]["refunds_this_month"] == round(24.99 + 45.00, 2)
    assert data["not_income"]["own_money_this_month"] == 150.0
    payers = {p["merchant_key"]: p for p in data["payers"]}
    assert payers["acme software"]["source"] == "Salary" and payers["acme software"]["months"] == 6


def test_a_student_payment_is_not_mistaken_for_a_transfer(fresh_db):
    """£30 from a student into HSBC, and £30 you paid someone from Starling
    two days later: equal amounts, different people."""
    starling = Account(provider="starling", external_id="st", name="Starling", kind="current")
    hsbc = Account(provider="truelayer", external_id="hs", name="HSBC", kind="current")
    amex = Account(provider="truelayer", external_id="ax", name="Amex", kind="credit_card")
    fresh_db.add_all([starling, hsbc, amex])
    fresh_db.commit()
    day = TODAY - dt.timedelta(days=5)
    sync_service.upsert_transactions(fresh_db, hsbc, [_raw("student", day, 30.00, "S WRIGHT", "transfer")])
    sync_service.upsert_transactions(
        fresh_db, starling, [_raw("football", day + dt.timedelta(days=2), -30.00, "LEYTON 5 A SIDE", "transfer")]
    )
    # A card purchase is never half of a transfer either.
    sync_service.upsert_transactions(fresh_db, amex, [_raw("meal", day, -30.00, "DISHOOM", "card")])
    assert detect_transfers(fresh_db) == 0

    # Your own money between current accounts arrives the same day.
    sync_service.upsert_transactions(fresh_db, starling, [_raw("me-out", day, -250.00, "S DOE", "transfer")])
    sync_service.upsert_transactions(fresh_db, hsbc, [_raw("me-in", day, 250.00, "S DOE", "transfer")])
    assert detect_transfers(fresh_db) == 1


def test_not_a_transfer_sticks(fresh_db):
    starling = Account(provider="starling", external_id="st", name="Starling", kind="current")
    hsbc = Account(provider="truelayer", external_id="hs", name="HSBC", kind="current")
    fresh_db.add_all([starling, hsbc])
    fresh_db.commit()
    sync_service.upsert_transactions(fresh_db, starling, [_raw("o", TODAY, -60.00, "R JONES", "transfer")])
    sync_service.upsert_transactions(fresh_db, hsbc, [_raw("i", TODAY, 60.00, "R JONES", "transfer")])
    assert detect_transfers(fresh_db) == 1

    credit = fresh_db.scalar(select(Transaction).where(Transaction.external_id == "i"))
    # What "not a transfer" on the Transactions tab stores.
    credit.is_transfer, credit.transfer_group = False, NOT_A_TRANSFER
    credit.category = sync_service.categorise_one(fresh_db, credit)
    fresh_db.commit()
    assert detect_transfers(fresh_db) == 0
    fresh_db.refresh(credit)
    debit = fresh_db.scalar(select(Transaction).where(Transaction.external_id == "o"))
    assert (credit.is_transfer, credit.category) == (False, "Income")
    assert (debit.is_transfer, debit.category) == (False, "Uncategorised")  # its other half is spending again


def test_a_payer_rule_only_applies_to_money_from_them():
    rule = CategoryRule(pattern="r jones", match_type="contains", field="merchant", category="Tutoring", priority=100)
    assert categorise(merchant="R JONES", description="", amount_minor=4000, rules=[rule]) == ("Tutoring", "rule")
    # Paying R Jones back isn't tutoring spending.
    assert categorise(merchant="R JONES", description="", amount_minor=-4000, rules=[rule])[0] == "Uncategorised"


def test_marking_the_tutoring_account(client):
    accounts = client.get("/api/accounts").json()["accounts"]
    target = accounts[0]
    response = client.patch(f"/api/accounts/{target['id']}", json={"income_default": "Tutoring"})
    assert response.status_code == 200 and response.json()["income_default"] == "Tutoring"
    assert client.patch(f"/api/accounts/{target['id']}", json={"income_default": "Lottery"}).status_code == 400
    cleared = client.patch(f"/api/accounts/{target['id']}", json={"income_default": ""})
    assert cleared.json()["income_default"] is None


def test_income_endpoint(client):
    data = client.get("/api/income", params={"months": 6}).json()
    assert [s["label"] for s in data["sources"]] == ["Salary", "Tutoring", "Other income"]
    assert len(data["series"]) == 6


def test_a_refund_mentioning_a_lesson_is_not_tutoring():
    assert categorise(merchant="DRIVING LESSON REFUND", description="", amount_minor=3500)[0] != "Tutoring"
    assert categorise(merchant="UNIVERSITY TUITION FEE REFUND", description="", amount_minor=9000)[0] == "Education"
    assert categorise(merchant="HMRC TAX REFUND", description="", amount_minor=21240)[0] == "Income"


def test_one_transfer_pair_does_not_make_a_name_yours(fresh_db):
    """You pay someone £60 and they pay you £60 the same day: one pair, two
    rows with their name. That isn't enough to call the name yours."""
    starling = Account(provider="starling", external_id="st", name="Starling", kind="current")
    hsbc = Account(provider="truelayer", external_id="hs", name="HSBC", kind="current")
    fresh_db.add_all([starling, hsbc])
    fresh_db.commit()
    sync_service.upsert_transactions(fresh_db, starling, [_raw("o", TODAY, -60.00, "R JONES", "transfer")])
    sync_service.upsert_transactions(fresh_db, hsbc, [_raw("i", TODAY, 60.00, "R JONES", "transfer")])
    detect_transfers(fresh_db)
    rows = list(fresh_db.scalars(select(Transaction)))
    assert income_service.own_names(rows) == set()

    day = TODAY - dt.timedelta(days=10)
    sync_service.upsert_transactions(fresh_db, starling, [_raw("o2", day, -75.00, "R JONES", "transfer")])
    sync_service.upsert_transactions(fresh_db, hsbc, [_raw("i2", day, 75.00, "R JONES", "transfer")])
    detect_transfers(fresh_db)
    assert income_service.own_names(list(fresh_db.scalars(select(Transaction)))) == {"r jones"}


def test_a_payer_rule_uses_the_stored_name(fresh_db):
    """A stored name can contain words that normalising would strip again;
    the rule must be on the name as stored, and touch no one else."""
    hsbc = Account(provider="truelayer", external_id="hs", name="HSBC", kind="current")
    fresh_db.add(hsbc)
    fresh_db.commit()
    sync_service.upsert_transactions(
        fresh_db,
        hsbc,
        [
            _raw("a", TODAY, 40.00, "ALICE PAYMENT-FROM SMITH", "transfer"),
            _raw("b", TODAY, 25.00, "ALICE SMITH", "transfer"),
        ],
    )
    stored = fresh_db.scalar(select(Transaction).where(Transaction.external_id == "a")).merchant_key
    assert stored == "alice payment from smith"
    saved = categorise_merchant(
        MerchantCategory(merchant="Alice Payment From Smith", merchant_key=stored, category="Tutoring"), db=fresh_db
    )
    assert saved["pattern"] == stored
    assert _categories(fresh_db) == {"a": "Tutoring", "b": "Income"}


def test_a_salary_from_a_payer_named_like_a_shop(fresh_db):
    """An employer whose name is also a shop's: BACS pay is salary, the
    occasional card refund from the same name is still a refund."""
    starling = Account(provider="starling", external_id="st", name="Starling", kind="current")
    hsbc = Account(provider="truelayer", external_id="hs", name="HSBC", kind="current")
    fresh_db.add_all([starling, hsbc])
    fresh_db.commit()
    rows_st = [_raw(f"st-{n}", _month(n, 28), 1900.00, "TESCO PLC", "transfer", "INCOME") for n in range(1, 4)]
    rows_hs = [_raw(f"hs-{n}", _month(n, 27), 450.00, "TESCO STORES PLC", "transfer") for n in range(1, 4)]
    rows_hs.append(_raw("refund", TODAY, 12.50, "TESCO STORES 3315", "card"))
    sync_service.upsert_transactions(fresh_db, starling, rows_st)
    sync_service.upsert_transactions(fresh_db, hsbc, rows_hs)
    sync_service.recategorise_all(fresh_db)
    got = _categories(fresh_db)
    assert {got[f"st-{n}"] for n in range(1, 4)} == {"Salary"}  # the bank said income
    assert {got[f"hs-{n}"] for n in range(1, 4)} == {"Salary"}  # paid like a salary
    assert got["refund"] == "Groceries"
