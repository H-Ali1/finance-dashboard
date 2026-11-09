"""Rent is a recurring bill, never an "unusual" payment.

Rent could show as unusual next to many small uncategorised payments. These
rebuild the ways that happens: a standing order reported through TrueLayer, a
payee whose name changes between payments, rent paid from one bank and then
another, and too little history. Names and amounts are made up.
"""

from __future__ import annotations

import datetime as dt

from app.analytics import anomalies, cashflow, insights, recurring
from app.models import Account
from app.providers.base import RawTransaction
from app.services.sync import upsert_transactions

TODAY = dt.date.today()
RENT = -85000


def _account(db, provider: str, name: str) -> Account:
    account = Account(provider=provider, external_id=f"{provider}-{name}", name=name, provider_meta={})
    db.add(account)
    db.commit()
    return account


def _months_ago(months: int) -> dt.date:
    """Today, a month back, two months back: the newest is always this month."""
    return TODAY - dt.timedelta(days=30 * months)


def _rent(n: int, description: str = "EXAMPLE HOMES", payment_type: str | None = "standing_order"):
    return RawTransaction(
        external_id=f"tl:rent-{n}-{description}",
        booked_on=_months_ago(n),
        amount_minor=RENT,
        description=description,
        merchant=description,
        payment_type=payment_type,
    )


def _everyday_spending(db, account: Account) -> None:
    """Plenty of small uncategorised payments, next to which rent looks big."""
    rows = [
        RawTransaction(
            external_id=f"small-{n}",
            booked_on=TODAY - dt.timedelta(days=n * 3 + 1),
            amount_minor=-(870 + (n % 5) * 50),
            description=f"SQ *MARKET STALL {n % 7}",
            payment_type="card",
        )
        for n in range(60)
    ]
    upsert_transactions(db, account, rows)


def _rent_item(db) -> dict | None:
    return next((i for i in recurring.detect_recurring(db) if "example homes" in i["merchant_key"]), None)


def _flagged(db) -> list[dict]:
    return [a for a in anomalies.detect_anomalies(db) if "example homes" in a["merchant"].lower()]


def test_two_standing_orders_are_enough_for_rent(fresh_db):
    """HSBC through TrueLayer may only give the last 89 days: two rent payments."""
    hsbc = _account(fresh_db, "truelayer", "Advance")
    _everyday_spending(fresh_db, hsbc)
    upsert_transactions(fresh_db, hsbc, [_rent(1), _rent(0)])

    item = _rent_item(fresh_db)
    assert item is not None
    assert (item["category"], item["type"], item["cadence"]) == ("Rent & Mortgage", "bill", "monthly")
    assert item["amount"] == 850.0 and item["status"] == "active"
    assert _flagged(fresh_db) == []


def test_a_changing_payee_name_is_still_one_bill(fresh_db):
    """The reference creeps into the payee: three names for one landlord."""
    hsbc = _account(fresh_db, "truelayer", "Advance")
    _everyday_spending(fresh_db, hsbc)
    upsert_transactions(
        fresh_db,
        hsbc,
        [
            _rent(4, "EXAMPLE HOMES", payment_type=None),  # read before payment types were kept
            _rent(3, "EXAMPLE HOMES LTD", payment_type=None),
            _rent(2, "EXAMPLE HOMES FLAT B", payment_type=None),
            _rent(1, "EXAMPLE HOMES"),
            _rent(0, "EXAMPLE HOMES DEC"),
        ],
    )
    item = _rent_item(fresh_db)
    assert item is not None and item["occurrences"] == 5
    assert {"example homes", "example homes flat b", "example homes dec"} <= set(item["merchant_keys"])
    assert _flagged(fresh_db) == []
    # Fixed costs and the month's projection count it once, as a commitment.
    assert set(item["merchant_keys"]) <= recurring.recurring_keys(recurring.detect_recurring(fresh_db))


def test_rent_paid_from_two_banks_is_one_bill(fresh_db):
    starling = _account(fresh_db, "starling", "Personal")
    hsbc = _account(fresh_db, "truelayer", "Advance")
    _everyday_spending(fresh_db, hsbc)
    upsert_transactions(
        fresh_db, starling, [_rent(3, "Example Homes", "transfer"), _rent(2, "Example Homes", "transfer")]
    )
    upsert_transactions(fresh_db, hsbc, [_rent(1), _rent(0)])

    item = _rent_item(fresh_db)
    assert item is not None and item["occurrences"] == 4
    assert _flagged(fresh_db) == []


def test_rent_is_never_unusual_even_the_first_time(fresh_db):
    """A new tenancy: one payment, no history to call it a pattern yet."""
    hsbc = _account(fresh_db, "truelayer", "Advance")
    _everyday_spending(fresh_db, hsbc)
    upsert_transactions(fresh_db, hsbc, [_rent(0, payment_type="transfer")])
    assert _flagged(fresh_db) == []
    titles = [f["title"] for f in insights.build_insights(fresh_db)]
    assert not any("Example Homes" in t for t in titles)


def test_a_standing_order_repeated_at_the_same_size_is_not_unusual(fresh_db):
    """Even when nothing names it as rent: a payee you pay the same sum each month."""
    hsbc = _account(fresh_db, "truelayer", "Advance")
    _everyday_spending(fresh_db, hsbc)
    rows = [
        RawTransaction(
            external_id=f"so-{n}",
            booked_on=_months_ago(n),
            amount_minor=-45000,
            description="M KHAN",
            payment_type="standing_order",
        )
        for n in (1, 0)
    ]
    upsert_transactions(fresh_db, hsbc, rows)
    assert [a for a in anomalies.detect_anomalies(fresh_db) if a["merchant"] == "M Khan"] == []


def test_uncategorised_is_no_baseline_for_unusual(fresh_db):
    """'Well above your typical uncategorised spend' compares unlike things."""
    hsbc = _account(fresh_db, "truelayer", "Advance")
    _everyday_spending(fresh_db, hsbc)
    upsert_transactions(
        fresh_db,
        hsbc,
        [RawTransaction(external_id="big", booked_on=TODAY, amount_minor=-48000, description="SOMEONE NEW")],
    )
    flagged = [a for a in anomalies.detect_anomalies(fresh_db) if a["merchant"] == "Someone New"]
    assert all(a["severity"] != "warning" for a in flagged)
    assert all("uncategorised spend" not in r for a in flagged for r in a["reasons"])


def test_a_real_outlier_in_a_real_category_is_still_flagged(fresh_db):
    card = _account(fresh_db, "truelayer", "Amex")
    rows = [
        RawTransaction(
            external_id=f"eat-{n}",
            booked_on=TODAY - dt.timedelta(days=n * 4 + 1),
            amount_minor=-(1200 + (n % 4) * 150),
            description=f"PRET A MANGER {n}",
            payment_type="card",
        )
        for n in range(30)
    ]
    rows.append(
        RawTransaction(
            external_id="feast",
            booked_on=TODAY,
            amount_minor=-41000,
            description="DISHOOM KINGS CROSS",
            payment_type="card",
        )
    )
    upsert_transactions(fresh_db, card, rows)
    flagged = [a for a in anomalies.detect_anomalies(fresh_db) if a["merchant"].startswith("Dishoom")]
    assert flagged and flagged[0]["severity"] == "warning"


def test_rent_counts_as_a_fixed_commitment(fresh_db):
    hsbc = _account(fresh_db, "truelayer", "Advance")
    upsert_transactions(fresh_db, hsbc, [_rent(2), _rent(1), _rent(0)])
    flow = cashflow.cashflow(fresh_db, months=3)
    assert flow["fixed_so_far"] == 850.0


def test_payees_join_on_two_words_not_a_surname():
    assert recurring.payee("example homes flat b") == recurring.payee("example homes") == "example homes"
    assert recurring.payee("m khan") != recurring.payee("a khan")


def test_one_service_on_two_cards_at_once_is_not_joined(fresh_db):
    """Two payments on each card, a fortnight apart from each other: joined,
    they would look like one fortnightly bill at double the cost."""
    card_a = _account(fresh_db, "truelayer", "Card A")
    card_b = _account(fresh_db, "truelayer", "Card B")
    for account, days in ((card_a, (60, 30)), (card_b, (45, 15))):
        upsert_transactions(
            fresh_db,
            account,
            [
                RawTransaction(
                    external_id=f"{account.name}-{d}",
                    booked_on=TODAY - dt.timedelta(days=d),
                    amount_minor=-999,
                    description="EXAMPLE MUSIC",
                    payment_type="card",
                )
                for d in days
            ],
        )
    assert [i for i in recurring.detect_recurring(fresh_db) if i["merchant_key"] == "example music"] == []


def test_a_bill_on_one_card_is_found_when_another_card_overlaps(fresh_db):
    """Card B pays monthly under two names; card A pays the same service
    twice in between. Card B's bill is still found; card A stays apart."""
    card_a = _account(fresh_db, "truelayer", "Card A")
    card_b = _account(fresh_db, "truelayer", "Card B")

    def gym(account, days, name):
        return [
            RawTransaction(
                external_id=f"{account.name}-{d}",
                booked_on=TODAY - dt.timedelta(days=d),
                amount_minor=-2999,
                description=name,
                payment_type="card",
            )
            for d in days
        ]

    upsert_transactions(
        fresh_db, card_b, gym(card_b, (120, 90), "EXAMPLE GYM") + gym(card_b, (60, 30), "EXAMPLE GYM CLUB")
    )
    upsert_transactions(fresh_db, card_a, gym(card_a, (75, 45), "EXAMPLE GYM"))
    items = [i for i in recurring.detect_recurring(fresh_db) if i["family"] == "example gym"]
    assert [(i["account_id"], i["occurrences"]) for i in items] == [(card_b.id, 4)]
