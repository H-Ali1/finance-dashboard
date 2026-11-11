"""Money moved between your own banks with the reference "internal": paired
when both sides are here, and never income or spending when only one is.
Names and amounts are made up."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app.models import Account, Transaction
from app.providers.base import RawTransaction
from app.services import sync as sync_service
from app.services.transfers import detect_transfers

TODAY = dt.date.today()


def _raw(uid, days_ago, pence, description, payment_type=None):
    return RawTransaction(
        external_id=uid,
        booked_on=TODAY - dt.timedelta(days=days_ago),
        amount_minor=pence,
        description=description,
        merchant=description,
        payment_type=payment_type,
    )


def _accounts(db):
    sender = Account(provider="truelayer", external_id="tl", name="Bank A", kind="current", provider_meta={})
    receiver = Account(provider="csv", external_id="manual:b", name="Bank B", kind="current", provider_meta={})
    db.add_all([sender, receiver])
    db.commit()
    return sender, receiver


def _categories(db):
    return {t.external_id: (t.category, t.is_transfer) for t in db.scalars(select(Transaction))}


def test_internal_transfer_days_apart_is_paired(fresh_db):
    sender, receiver = _accounts(fresh_db)
    sync_service.upsert_transactions(fresh_db, sender, [_raw("out", 5, -25000, "SAM BANK B INTERNAL", "transfer")])
    sync_service.upsert_transactions(fresh_db, receiver, [_raw("in", 2, 25000, "From DOE S - internal")])
    assert detect_transfers(fresh_db) == 1  # three days between the banks' dates
    assert {v[1] for v in _categories(fresh_db).values()} == {True}


def test_a_lone_internal_transfer_is_neither_income_nor_spending(fresh_db):
    sender, receiver = _accounts(fresh_db)
    sync_service.upsert_transactions(fresh_db, receiver, [_raw("in", 3, 30000, "From DOE S - internal")])
    sync_service.upsert_transactions(fresh_db, sender, [_raw("out", 40, -12000, "SAM BANK B INTERNAL", "transfer")])
    detect_transfers(fresh_db)
    sync_service.recategorise_all(fresh_db)
    got = _categories(fresh_db)
    assert got["in"][0] == "Transfers" and got["out"][0] == "Transfers"


def test_a_category_set_by_hand_is_kept(fresh_db):
    _, receiver = _accounts(fresh_db)
    sync_service.upsert_transactions(fresh_db, receiver, [_raw("in", 3, 30000, "From DOE S - internal")])
    row = fresh_db.scalar(select(Transaction))
    row.category, row.category_source = "Income", "manual"
    fresh_db.commit()
    detect_transfers(fresh_db)
    sync_service.recategorise_all(fresh_db)
    fresh_db.refresh(row)
    assert row.category == "Income"
