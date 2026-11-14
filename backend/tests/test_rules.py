"""Rules with several conditions and actions: matching, which rule wins,
renaming and excluding (and undoing both), the preview, checks on what's
saved, and the upgrade of rules saved by older versions.

Names and amounts are made up.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app import migrations
from app.models import Account, CategoryRule, Transaction
from app.providers.base import RawTransaction
from app.routers.transactions import (
    categorise_merchant,
    create_rule,
    delete_rule,
    preview_rule,
    update_rule,
    update_transaction,
)
from app.schemas import MerchantCategory, RuleCreate, RulePreview, TransactionUpdate
from app.services import sync as sync_service
from app.services.categorise import categorise, evaluate_rules, rule_order

TODAY = dt.date.today()


def _account(db, name: str = "Rules test") -> Account:
    account = Account(provider="csv", external_id=f"manual:{name}", name=name, institution_name="Test bank")
    db.add(account)
    db.commit()
    return account


def _pay(db, account: Account, *rows: tuple[str, int], payment_type: str | None = None) -> list[Transaction]:
    raws = [
        RawTransaction(
            external_id=f"{description}-{n}",
            booked_on=TODAY - dt.timedelta(days=n),
            amount_minor=amount,
            description=description,
            payment_type=payment_type,
        )
        for n, (description, amount) in enumerate(rows)
    ]
    sync_service.upsert_transactions(db, account, raws)
    return list(db.scalars(select(Transaction).where(Transaction.account_id == account.id)))


def _row(db, description: str) -> Transaction:
    return db.scalar(select(Transaction).where(Transaction.description == description))


# --- matching and precedence ---------------------------------------------------------
def test_a_rule_with_an_amount_beats_the_same_text_without():
    plain = CategoryRule(id=1, pattern="example rail", match_type="words", field="merchant", category="Transport")
    long_trip = CategoryRule(
        id=2, pattern="example rail", match_type="words", field="merchant", amount_min_minor=2000, category="Travel"
    )
    rules = sorted([plain, long_trip], key=rule_order)
    assert categorise(merchant="EXAMPLE RAIL", description="", amount_minor=-4500, rules=rules)[0] == "Travel"
    assert categorise(merchant="EXAMPLE RAIL", description="", amount_minor=-350, rules=rules)[0] == "Transport"


def test_every_condition_has_to_hold():
    rule = CategoryRule(id=1, direction="in", account_id=7, payment_type="transfer", category="Tutoring")

    def outcome(amount: int, account: int, payment: str):
        return evaluate_rules(
            [rule],
            bank_key="sample payer",
            description="",
            amount_minor=amount,
            account_id=account,
            payment_type=payment,
        ).category

    assert outcome(3000, 7, "transfer") == "Tutoring"
    assert outcome(3000, 8, "transfer") is None
    assert outcome(3000, 7, "card") is None
    assert outcome(-3000, 7, "transfer") is None


def test_a_rule_naming_the_payer_beats_one_that_does_not():
    by_account = CategoryRule(id=1, direction="in", account_id=7, payment_type="transfer", category="Tutoring")
    by_payer = CategoryRule(id=2, pattern="sample payer", match_type="words", category="Gifts & Charity")
    rules = sorted([by_account, by_payer], key=rule_order)
    found = evaluate_rules(
        rules, bank_key="sample payer", description="", amount_minor=3000, account_id=7, payment_type="transfer"
    )
    assert found.category == "Gifts & Charity"


def test_a_rule_without_conditions_matches_nothing():
    rule = CategoryRule(id=1, category="Shopping")
    assert evaluate_rules([rule], bank_key="anything", description="", amount_minor=-100).category is None


def test_rules_sharing_a_pattern_can_both_be_saved(fresh_db):
    account = _account(fresh_db)
    _pay(fresh_db, account, ("EXAMPLE RAIL", -4500), ("EXAMPLE RAIL", -350))
    create_rule(RuleCreate(pattern="example rail", match_type="words", category="Transport"), db=fresh_db)
    create_rule(RuleCreate(pattern="example rail", match_type="words", amount_min=20, category="Travel"), db=fresh_db)
    rows = fresh_db.scalars(select(Transaction).order_by(Transaction.amount_minor)).all()
    assert [(r.amount_minor, r.category) for r in rows] == [(-4500, "Travel"), (-350, "Transport")]


# --- renaming -----------------------------------------------------------------------
def test_a_rename_merges_merchants_and_undoes_itself_when_deleted(fresh_db):
    account = _account(fresh_db)
    _pay(fresh_db, account, ("KESTREL NORTH", -1200), ("KESTREL SOUTH", -800))
    saved = create_rule(RuleCreate(pattern="kestrel", match_type="words", rename_to="Kestrel Foods"), db=fresh_db)
    rename = saved["id"]
    assert saved["recategorised"] == 2  # renamed counts as changed, not only a new category
    north, south = _row(fresh_db, "KESTREL NORTH"), _row(fresh_db, "KESTREL SOUTH")
    assert {(t.merchant, t.merchant_key, t.merchant_source) for t in (north, south)} == {
        ("Kestrel Foods", "kestrel foods", "rule")
    }

    # A category for the new name applies to both.
    categorise_merchant(MerchantCategory(merchant="Kestrel Foods", category="Groceries"), db=fresh_db)
    assert {north.category, south.category} == {"Groceries"}

    delete_rule(rename, db=fresh_db)
    assert (north.merchant, north.merchant_key, north.merchant_source) == ("Kestrel North", "kestrel north", None)
    assert south.merchant_key == "kestrel south"
    # The category rule was on the new name, so it no longer applies.
    assert {north.category, south.category} == {"Uncategorised"}


def test_a_rule_never_renames_a_merchant_you_named(fresh_db):
    account = _account(fresh_db)
    (txn,) = _pay(fresh_db, account, ("KESTREL EAST", -500))
    update_transaction(txn.id, TransactionUpdate(merchant="Corner shop"), db=fresh_db)
    create_rule(RuleCreate(pattern="kestrel", match_type="words", rename_to="Kestrel Foods"), db=fresh_db)
    assert (txn.merchant, txn.merchant_source, txn.bank_merchant) == ("Corner shop", "manual", "Kestrel East")
    # The bank's name still matches rules that do other things.
    create_rule(RuleCreate(pattern="kestrel east", match_type="words", category="Groceries"), db=fresh_db)
    assert txn.category == "Groceries"


def test_new_payments_arrive_renamed(fresh_db):
    account = _account(fresh_db)
    create_rule(RuleCreate(pattern="kestrel", match_type="words", rename_to="Kestrel Foods"), db=fresh_db)
    (txn,) = _pay(fresh_db, account, ("KESTREL NORTH", -1200))
    assert (txn.merchant, txn.bank_merchant) == ("Kestrel Foods", "Kestrel North")


# --- excluding ----------------------------------------------------------------------
def test_an_exclusion_follows_the_rule_but_your_say_is_final(fresh_db):
    account = _account(fresh_db)
    _pay(fresh_db, account, ("FENWORTH OAK", -2500), ("FENWORTH MILL", -1500))
    rule = create_rule(RuleCreate(pattern="fenworth", match_type="words", exclude=True), db=fresh_db)["id"]
    oak, mill = _row(fresh_db, "FENWORTH OAK"), _row(fresh_db, "FENWORTH MILL")
    assert {(t.excluded, t.excluded_source) for t in (oak, mill)} == {(True, "rule")}

    update_transaction(mill.id, TransactionUpdate(excluded=False), db=fresh_db)
    sync_service.recategorise_all(fresh_db)
    assert (mill.excluded, mill.excluded_source) == (False, "manual")

    delete_rule(rule, db=fresh_db)
    assert (oak.excluded, oak.excluded_source) == (False, None)


# --- the preview --------------------------------------------------------------------
def test_the_preview_shows_what_would_change_without_saving(fresh_db):
    account = _account(fresh_db)
    _pay(fresh_db, account, ("KESTREL NORTH", -1200), ("KESTREL SOUTH", -800), ("FENWORTH OAK", -300))
    result = preview_rule(
        RulePreview(pattern="kestrel", match_type="words", category="Groceries", rename_to="Kestrel Foods"),
        db=fresh_db,
    )
    assert (result["matches"], result["changes"]) == (2, 2)
    assert {(e["new_merchant"], e["new_category"]) for e in result["examples"]} == {("Kestrel Foods", "Groceries")}
    assert fresh_db.scalar(select(CategoryRule)) is None
    assert _row(fresh_db, "KESTREL NORTH").category == "Uncategorised"


def test_the_preview_of_an_edit_counts_only_real_changes(fresh_db):
    account = _account(fresh_db)
    _pay(fresh_db, account, ("KESTREL NORTH", -1200), ("KESTREL SOUTH", -800))
    rule_id = create_rule(RuleCreate(pattern="kestrel", match_type="words", category="Groceries"), db=fresh_db)["id"]
    same = preview_rule(
        RulePreview(pattern="kestrel", match_type="words", category="Groceries", rule_id=rule_id), db=fresh_db
    )
    assert (same["matches"], same["changes"]) == (2, 0)


# --- what can be saved --------------------------------------------------------------
@pytest.mark.parametrize(
    "payload",
    [
        RuleCreate(category="Shopping"),  # no condition
        RuleCreate(pattern="kestrel"),  # nothing to do
        RuleCreate(pattern="(", match_type="regex", category="Shopping"),
        RuleCreate(pattern="kestrel", amount_min=50, amount_max=10, category="Shopping"),
        RuleCreate(pattern="kestrel", payment_type="cheque", category="Shopping"),
        RuleCreate(pattern="kestrel", account_id=999, category="Shopping"),
    ],
)
def test_a_rule_that_cannot_work_is_refused(fresh_db, payload):
    with pytest.raises(HTTPException) as refused:
        create_rule(payload, db=fresh_db)
    assert refused.value.status_code in (400, 404)
    assert fresh_db.scalar(select(CategoryRule)) is None


def test_an_edit_cannot_copy_another_rules_conditions(fresh_db):
    first = create_rule(RuleCreate(pattern="kestrel", category="Groceries", apply_now=False), db=fresh_db)["id"]
    second = create_rule(RuleCreate(pattern="fenworth", category="Shopping", apply_now=False), db=fresh_db)["id"]
    with pytest.raises(HTTPException) as refused:
        update_rule(second, RuleCreate(pattern="kestrel", category="Shopping", apply_now=False), db=fresh_db)
    assert refused.value.status_code == 409

    update_rule(first, RuleCreate(pattern="kestrel", amount_max=40, category="Groceries", apply_now=False), db=fresh_db)
    assert fresh_db.get(CategoryRule, first).amount_max_minor == 4000


def test_an_edit_keeps_the_priority_a_merchant_choice_gave_it(fresh_db):
    fresh_db.add(CategoryRule(pattern="kestrel", match_type="contains", field="merchant", category="Shopping"))
    fresh_db.commit()
    rule_id = categorise_merchant(MerchantCategory(merchant="Kestrel", category="Groceries"), db=fresh_db)["rule_id"]
    before = fresh_db.get(CategoryRule, rule_id).priority
    update_rule(rule_id, RuleCreate(pattern="kestrel", match_type="words", category="Eating Out"), db=fresh_db)
    assert fresh_db.get(CategoryRule, rule_id).priority == before < 100


# --- upgrading ----------------------------------------------------------------------
def test_rules_from_an_older_version_are_carried_over(tmp_path):
    path = tmp_path / "v5.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        "CREATE TABLE category_rules (id INTEGER PRIMARY KEY, pattern VARCHAR(160), match_type VARCHAR(16), "
        "field VARCHAR(16), category VARCHAR(48), priority INTEGER, created_at DATETIME);"
        "CREATE UNIQUE INDEX uq_rule_pattern ON category_rules (field, match_type, pattern);"
        "INSERT INTO category_rules (pattern, match_type, field, category, priority, created_at) "
        "VALUES ('kestrel', 'words', 'merchant', 'Groceries', 90, '2026-01-01');"
        "CREATE TABLE transactions (id INTEGER PRIMARY KEY, excluded BOOLEAN);"
        "INSERT INTO transactions (excluded) VALUES (1), (0);"
        "PRAGMA user_version = 5;"
    )
    raw.commit()
    raw.close()

    engine = create_engine(f"sqlite:///{path.as_posix()}")
    migrations.migrate(engine)
    fresh = create_engine(f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}")
    migrations.migrate(fresh)
    upgraded, built = inspect(engine), inspect(fresh)
    assert {c["name"] for c in upgraded.get_columns("category_rules")} == {
        c["name"] for c in built.get_columns("category_rules")
    }
    columns = {c["name"] for c in upgraded.get_columns("transactions")}
    assert {"merchant_source", "bank_merchant", "bank_merchant_key", "excluded_source"} <= columns
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT excluded, excluded_source FROM transactions ORDER BY id").all() == [
            (1, "manual"),
            (0, None),
        ]

    db = sessionmaker(bind=engine, expire_on_commit=False)()
    (old,) = db.scalars(select(CategoryRule)).all()
    assert (old.pattern, old.category, old.priority, old.exclude) == ("kestrel", "Groceries", 90, False)
    # The same text with an amount is a rule of its own now...
    db.add(
        CategoryRule(pattern="kestrel", match_type="words", field="merchant", amount_min_minor=100, category="Travel")
    )
    db.commit()
    # ...but the same conditions twice still aren't.
    db.add(CategoryRule(pattern="kestrel", match_type="words", field="merchant", category="Shopping"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.close()
    engine.dispose()
    fresh.dispose()


def test_the_preview_shows_matches_before_an_action_is_chosen(fresh_db):
    account = _account(fresh_db)
    _pay(fresh_db, account, ("KESTREL NORTH", -1200), ("FENWORTH OAK", -300))
    result = preview_rule(RulePreview(pattern="kestrel", match_type="words"), db=fresh_db)
    assert (result["matches"], result["changes"]) == (1, 0)


# --- review follow-ups ----------------------------------------------------------------
def test_a_largest_amount_of_zero_is_kept(fresh_db):
    """ "Up to £0" is a condition; it isn't dropped as if it were no limit."""
    account = _account(fresh_db)
    _pay(fresh_db, account, ("KESTREL NORTH", -500))
    rule_id = create_rule(RuleCreate(pattern="kestrel", amount_max=0, exclude=True), db=fresh_db)["id"]
    assert fresh_db.get(CategoryRule, rule_id).amount_max_minor == 0
    assert _row(fresh_db, "KESTREL NORTH").excluded is False
    # On its own it's still a condition.
    create_rule(RuleCreate(amount_max=0, category="Fees & Interest", apply_now=False), db=fresh_db)


def test_a_priority_of_zero_comes_before_one():
    first = CategoryRule(id=1, pattern="pret", match_type="words", priority=0, category="Eating Out")
    second = CategoryRule(id=2, pattern="pret", match_type="words", priority=1, category="Shopping")
    rules = sorted([second, first], key=rule_order)
    assert categorise(merchant="PRET", description="", amount_minor=-450, rules=rules)[0] == "Eating Out"


def test_a_merchant_choice_beats_a_rule_at_priority_one(fresh_db):
    """Choosing a category then saves its rule at priority 0."""
    fresh_db.add(CategoryRule(pattern="pret", match_type="contains", field="merchant", category="Shopping", priority=1))
    fresh_db.commit()
    categorise_merchant(MerchantCategory(merchant="Pret", category="Eating Out"), db=fresh_db)
    rules = sync_service._rules(fresh_db)
    assert categorise(merchant="PRET", description="", amount_minor=-450, rules=rules)[0] == "Eating Out"


def test_the_preview_of_an_edit_shows_what_it_lets_go(fresh_db):
    account = _account(fresh_db)
    _pay(fresh_db, account, ("KESTREL NORTH", -1200), ("KESTREL SOUTH", -800))
    rule_id = create_rule(RuleCreate(pattern="kestrel", match_type="words", exclude=True), db=fresh_db)["id"]
    narrower = preview_rule(
        RulePreview(pattern="kestrel north", match_type="words", exclude=True, rule_id=rule_id), db=fresh_db
    )
    assert (narrower["matches"], narrower["changes"]) == (1, 1)
    (let_go,) = [e for e in narrower["examples"] if not e["matched"]]
    assert (let_go["description"], let_go["excluded"], let_go["new_excluded"]) == ("KESTREL SOUTH", True, False)

    # Saving does what the preview said.
    update_rule(rule_id, RuleCreate(pattern="kestrel north", match_type="words", exclude=True), db=fresh_db)
    assert _row(fresh_db, "KESTREL SOUTH").excluded is False


def test_the_preview_files_a_renamed_merchant_as_saving_does(fresh_db):
    account = _account(fresh_db)
    _pay(fresh_db, account, ("KESTREL NORTH", -450))
    rename = RuleCreate(pattern="kestrel", match_type="words", rename_to="Kestrel Cafe")
    (example,) = preview_rule(RulePreview(**rename.model_dump()), db=fresh_db)["examples"]
    assert (example["new_merchant"], example["new_category"]) == ("Kestrel Cafe", "Eating Out")

    create_rule(rename, db=fresh_db)
    row = _row(fresh_db, "KESTREL NORTH")
    assert (row.merchant, row.category) == ("Kestrel Cafe", "Eating Out")
