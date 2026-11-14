"""Categories learned from the merchants you've categorised: when a guess is
filed, when it's only suggested, and when it's left alone.

Names and amounts are made up, and none of them hits a keyword, so only what
was learned can place them.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app.models import Account, Transaction
from app.providers.base import RawTransaction
from app.routers.transactions import categorise_merchant, delete_rule, uncategorised_merchants, update_transaction
from app.schemas import MerchantCategory, TransactionUpdate
from app.services import learn
from app.services import sync as sync_service
from app.services.categorise import keyword_category

TODAY = dt.date.today()


def _account(db) -> Account:
    account = Account(provider="csv", external_id="manual:learning", name="Learning test", institution_name="Test")
    db.add(account)
    db.commit()
    return account


def _pay(db, account: Account, *descriptions: str, amount: int = -1500) -> None:
    raws = [
        RawTransaction(
            external_id=f"{description}-{n}",
            booked_on=TODAY - dt.timedelta(days=n),
            amount_minor=amount,
            description=description,
        )
        for n, description in enumerate(descriptions)
    ]
    sync_service.upsert_transactions(db, account, raws)


def _rows(db, description: str) -> list[Transaction]:
    return list(db.scalars(select(Transaction).where(Transaction.description == description)))


def _teach(db, merchant: str, category: str) -> int:
    return categorise_merchant(MerchantCategory(merchant=merchant, category=category), db=db)["rule_id"]


def test_the_names_used_here_hit_no_keyword():
    for name in ("FENWORTH OAK", "FENWORTH MILL", "FENWORTH YARD", "KESTREL NORTH", "KESTREL SOUTH"):
        assert keyword_category(name) is None


def test_a_merchant_like_two_you_categorised_is_filed(fresh_db):
    account = _account(fresh_db)
    _pay(fresh_db, account, "FENWORTH OAK", "FENWORTH MILL", "KESTREL NORTH", "KESTREL SOUTH", "FENWORTH YARD")
    _teach(fresh_db, "Fenworth Oak", "Shopping")
    _teach(fresh_db, "Fenworth Mill", "Shopping")
    _teach(fresh_db, "Kestrel North", "Groceries")
    _teach(fresh_db, "Kestrel South", "Groceries")
    (yard,) = _rows(fresh_db, "FENWORTH YARD")
    assert (yard.category, yard.category_source) == ("Shopping", learn.LEARNED)

    # Listed to check, and confirming it makes it a rule like any other.
    listed = uncategorised_merchants(db=fresh_db)
    assert [(g["merchant_key"], g["category"]) for g in listed["learned"]] == [("fenworth yard", "Shopping")]
    assert listed["learned_from"] == 4
    _teach(fresh_db, "Fenworth Yard", "Shopping")
    assert yard.category_source == "rule"


def test_one_example_is_only_a_suggestion(fresh_db):
    account = _account(fresh_db)
    _pay(fresh_db, account, "FENWORTH OAK", "KESTREL NORTH", "FENWORTH YARD")
    _teach(fresh_db, "Fenworth Oak", "Shopping")
    _teach(fresh_db, "Kestrel North", "Groceries")
    (yard,) = _rows(fresh_db, "FENWORTH YARD")
    assert (yard.category, yard.category_source) == ("Uncategorised", "auto")

    listed = uncategorised_merchants(db=fresh_db)
    (waiting,) = listed["merchants"]
    assert waiting["suggestion"]["category"] == "Shopping"
    assert waiting["suggestion"]["because"] == ["fenworth"]


def test_a_merchant_you_categorised_once_is_not_guessed_for(fresh_db):
    """ "Just this one" stays just that one."""
    account = _account(fresh_db)
    _pay(fresh_db, account, "FENWORTH OAK", "FENWORTH MILL", "KESTREL NORTH", "KESTREL SOUTH")
    _pay(fresh_db, account, "FENWORTH YARD", "FENWORTH YARD")
    for merchant, category in (
        ("Fenworth Oak", "Shopping"),
        ("Fenworth Mill", "Shopping"),
        ("Kestrel North", "Groceries"),
        ("Kestrel South", "Groceries"),
    ):
        _teach(fresh_db, merchant, category)
    first, second = _rows(fresh_db, "FENWORTH YARD")
    update_transaction(first.id, TransactionUpdate(category="Gifts & Charity"), db=fresh_db)
    sync_service.recategorise_all(fresh_db)
    assert (first.category, first.category_source) == ("Gifts & Charity", "manual")
    assert (second.category, second.category_source) == ("Uncategorised", "auto")


def test_a_guess_goes_when_what_it_learned_from_goes(fresh_db):
    account = _account(fresh_db)
    _pay(fresh_db, account, "FENWORTH OAK", "FENWORTH MILL", "KESTREL NORTH", "KESTREL SOUTH", "FENWORTH YARD")
    rules = [
        _teach(fresh_db, "Fenworth Oak", "Shopping"),
        _teach(fresh_db, "Fenworth Mill", "Shopping"),
        _teach(fresh_db, "Kestrel North", "Groceries"),
        _teach(fresh_db, "Kestrel South", "Groceries"),
    ]
    (yard,) = _rows(fresh_db, "FENWORTH YARD")
    assert yard.category_source == learn.LEARNED
    delete_rule(rules[0], db=fresh_db)
    assert (yard.category, yard.category_source) == ("Uncategorised", "auto")


def test_money_in_is_never_guessed(fresh_db):
    account = _account(fresh_db)
    _pay(fresh_db, account, "FENWORTH OAK", "FENWORTH MILL", "KESTREL NORTH", "KESTREL SOUTH")
    for merchant, category in (
        ("Fenworth Oak", "Shopping"),
        ("Fenworth Mill", "Shopping"),
        ("Kestrel North", "Groceries"),
        ("Kestrel South", "Groceries"),
    ):
        _teach(fresh_db, merchant, category)
    _pay(fresh_db, account, "FENWORTH REFUNDS DESK", amount=1500)
    sync_service.recategorise_all(fresh_db)
    (refund,) = _rows(fresh_db, "FENWORTH REFUNDS DESK")
    assert refund.category_source != learn.LEARNED


def test_each_merchant_counts_once():
    """A merchant paid daily doesn't outweigh one paid once."""

    class Row:
        def __init__(self, id_, key, category):
            self.id, self.merchant_key, self.merchant = id_, key, key
            self.category, self.category_source = category, "rule"
            self.amount_minor, self.is_transfer = -100, False
            self.description, self.mcc, self.payment_type = key, None, None
            self.booked_on = TODAY

    rows = [Row(n, "alder north", "Shopping") for n in range(50)] + [Row(99, "birch north", "Groceries")]
    learner = learn.Learner.from_rows(rows)
    assert len(learner.examples) == 2
    guess = learner.guess(learn.features("cedar north", "", None, None))
    assert guess is not None and guess.probability < 0.6


def test_renaming_merchants_into_one_keeps_what_was_learned(fresh_db):
    """Each bank name is still learned from, and guessed for, on its own."""
    from app.routers.transactions import create_rule
    from app.schemas import RuleCreate

    account = _account(fresh_db)
    _pay(fresh_db, account, "FENWORTH OAK", "FENWORTH MILL", "KESTREL NORTH", "KESTREL SOUTH", "KESTREL EAST")
    for merchant, category in (
        ("Fenworth Oak", "Shopping"),
        ("Fenworth Mill", "Shopping"),
        ("Kestrel North", "Groceries"),
        ("Kestrel South", "Groceries"),
    ):
        _teach(fresh_db, merchant, category)
    (east,) = _rows(fresh_db, "KESTREL EAST")
    assert (east.category, east.category_source) == ("Groceries", learn.LEARNED)

    create_rule(RuleCreate(pattern="kestrel", match_type="words", rename_to="Kestrel Foods"), db=fresh_db)
    assert (east.merchant, east.category, east.category_source) == ("Kestrel Foods", "Groceries", learn.LEARNED)
