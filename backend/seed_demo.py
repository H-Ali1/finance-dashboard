"""Populate the database with 14 months of realistic fake data.

Run this to see the dashboard fully populated before you connect anything real:

    python seed_demo.py            # add demo data
    python seed_demo.py --reset    # wipe everything first

The generated data deliberately contains things the insight engine should catch:
a subscription price rise, the same service billed on two cards, a double
charge, a spending spike, and a card balance being carried.
"""

from __future__ import annotations

import argparse
import datetime as dt
import random

from app.analytics.networth import snapshot_networth
from app.db import Base, SessionLocal, engine, init_db
from app.models import Budget, Connection, ManualAsset, NetWorthSnapshot
from app.money import to_minor
from app.providers.base import RawAccount, RawTransaction
from app.services.sync import upsert_account, upsert_transactions
from app.services.transfers import detect_transfers

random.seed(20260802)
TODAY = dt.date.today()
MONTHS = 14

ACCOUNTS = [
    ("starling", "starling-demo-current", "Starling Personal", "current", "Starling", 184_230),
    # No live feed for HSBC or Amex yet, so they come in as statement imports.
    ("csv", "hsbc-demo-current", "HSBC Current Account ••4417", "current", "HSBC", 92_640),
    ("csv", "hsbc-demo-savings", "HSBC Flexible Saver", "savings", "HSBC", 610_000),
    ("csv", "amex-demo-card", "Amex Platinum Cashback ••1009", "credit_card", "American Express", -138_450),
    ("trading212", "trading212-invest", "Trading 212", "investment", "Trading 212", 1_142_300),
]

GROCERS = ["TESCO STORES 3315 LONDON", "SAINSBURYS SMKT", "ALDI 812", "LIDL GB LONDON", "CO-OP GROUP 4421"]
EATING = ["PRET A MANGER 293", "GREGGS PLC", "NANDOS CHICKENLAND", "DELIVEROO", "UBER EATS", "COSTA COFFEE 8812", "FIVE GUYS SOHO"]
SHOPS = ["AMAZON.CO.UK*MK4TR", "ARGOS LTD", "ZARA UK", "JD SPORTS", "IKEA WEMBLEY", "SCREWFIX DIRECT"]
TRANSPORT = ["TFL TRAVEL CH", "UBER *TRIP", "TRAINLINE.COM", "RINGGO PARKING"]
FUEL = ["SHELL WEMBLEY", "BP CONNECT HANGER LANE", "ESSO EDGWARE RD"]


def _txn(day: dt.date, amount: float, description: str, seq: int, mcc: str | None = None) -> RawTransaction:
    return RawTransaction(
        external_id=f"demo-{day.isoformat()}-{seq}-{abs(hash(description)) % 100000}",
        booked_on=day,
        amount_minor=to_minor(amount),
        description=description,
        merchant=description,
        mcc=mcc,
    )


def _month_starts(count: int) -> list[dt.date]:
    starts = []
    year, month = TODAY.year, TODAY.month
    for _ in range(count):
        starts.append(dt.date(year, month, 1))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return sorted(starts)


def build() -> dict[str, list[RawTransaction]]:
    out: dict[str, list[RawTransaction]] = {key: [] for _, key, *_ in ACCOUNTS}
    seq = 0

    def add(account: str, day: dt.date, amount: float, description: str, mcc: str | None = None):
        nonlocal seq
        seq += 1
        if day <= TODAY:
            out[account].append(_txn(day, amount, description, seq, mcc))

    for index, start in enumerate(_month_starts(MONTHS)):
        last_day = 28
        # --- income: salary into Starling, a little freelance now and then ----
        salary = 3180.00 + (120 if index >= 8 else 0)  # a pay rise 6 months back
        add("starling-demo-current", start.replace(day=25), salary, "ACME SOFTWARE LTD SALARY")
        if index % 4 == 2:
            add("starling-demo-current", start.replace(day=14), 420.00, "FREELANCE INVOICE PAYMENT")

        # --- fixed outgoings --------------------------------------------------
        add("starling-demo-current", start.replace(day=1), -1150.00, "RENT PAYMENT LANDLORD")
        add("starling-demo-current", start.replace(day=3), -142.00, "COUNCIL TAX BRENT")
        add("starling-demo-current", start.replace(day=6), -68.40, "OCTOPUS ENERGY LTD")
        add("starling-demo-current", start.replace(day=6), -31.20, "THAMES WATER")
        add("starling-demo-current", start.replace(day=8), -34.99, "VODAFONE LTD")
        add("starling-demo-current", start.replace(day=8), -29.00, "VIRGIN MEDIA BROADBAND")
        add("hsbc-demo-current", start.replace(day=12), -61.85, "ADMIRAL INSURANCE CAR")

        # --- subscriptions, with a Netflix rise 5 months ago ------------------
        netflix = 10.99 if index < MONTHS - 5 else 12.99
        add("amex-demo-card", start.replace(day=17), -netflix, "NETFLIX.COM")
        add("amex-demo-card", start.replace(day=4), -11.99, "SPOTIFY UK")
        add("amex-demo-card", start.replace(day=9), -8.99, "APPLE.COM/BILL")
        add("hsbc-demo-current", start.replace(day=2), -32.00, "PUREGYM LIMITED")
        add("amex-demo-card", start.replace(day=21), -8.99, "AMAZON PRIME MEMBERSHIP")
        # ...and Prime also billed on HSBC — the duplicate the engine flags.
        add("hsbc-demo-current", start.replace(day=23), -8.99, "AMAZON PRIME MEMBERSHIP")
        if index >= MONTHS - 7:
            add("amex-demo-card", start.replace(day=11), -20.00, "OPENAI CHATGPT SUBSCR")

        # --- variable spending -------------------------------------------------
        for _ in range(random.randint(8, 13)):
            day = start.replace(day=random.randint(1, last_day))
            add("hsbc-demo-current", day, -round(random.uniform(9, 78), 2), random.choice(GROCERS), "5411")
        for _ in range(random.randint(6, 14)):
            day = start.replace(day=random.randint(1, last_day))
            add("amex-demo-card", day, -round(random.uniform(4, 42), 2), random.choice(EATING), "5812")
        for _ in range(random.randint(3, 7)):
            day = start.replace(day=random.randint(1, last_day))
            add("amex-demo-card", day, -round(random.uniform(12, 190), 2), random.choice(SHOPS), "5999")
        for _ in range(random.randint(4, 9)):
            day = start.replace(day=random.randint(1, last_day))
            add("starling-demo-current", day, -round(random.uniform(2.8, 24), 2), random.choice(TRANSPORT), "4111")
        for _ in range(random.randint(2, 4)):
            day = start.replace(day=random.randint(1, last_day))
            add("hsbc-demo-current", day, -round(random.uniform(48, 86), 2), random.choice(FUEL), "5541")

        # --- moving money about ------------------------------------------------
        add("starling-demo-current", start.replace(day=26), -450.00, "TRANSFER TO SAVINGS")
        add("hsbc-demo-savings", start.replace(day=26), 450.00, "TRANSFER FROM STARLING")
        add("starling-demo-current", start.replace(day=20), -300.00, "PAYMENT TO AMEX CARD")
        add("amex-demo-card", start.replace(day=20), 300.00, "PAYMENT RECEIVED THANK YOU")
        add("starling-demo-current", start.replace(day=27), -200.00, "TRADING 212 DEPOSIT")

        # --- the things the insight engine should catch -------------------------
        if index == MONTHS - 2:  # last complete month: a spending spike
            add("amex-demo-card", start.replace(day=15), -689.00, "CURRYS PC WORLD ONLINE", "5732")
            add("amex-demo-card", start.replace(day=16), -689.00, "CURRYS PC WORLD ONLINE", "5732")  # double charge
        if index == MONTHS - 3:
            add("hsbc-demo-current", start.replace(day=7), -18.50, "OVERDRAFT INTEREST")
            add("amex-demo-card", start.replace(day=19), -24.00, "NON-STERLING TRANSACTION FEE")
        if index in (MONTHS - 6, MONTHS - 5):
            add("amex-demo-card", start.replace(day=13), -1240.00, "RYANAIR FLIGHTS")
            add("amex-demo-card", start.replace(day=14), -318.00, "BOOKING.COM HOTEL")

    return out


def reset() -> None:
    """Drop everything, including the schema version, so migrations start over."""
    from app import models  # noqa: F401  (registers the mappers)

    Base.metadata.drop_all(bind=engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA user_version = 0")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="drop all tables first")
    args = parser.parse_args()
    seed(reset_first=args.reset)


def seed(reset_first: bool = False, verbose: bool = True) -> None:
    if reset_first:
        reset()
    init_db()

    db = SessionLocal()
    try:
        data = build()
        connections: dict[str, Connection] = {}
        for provider, external_id, name, kind, institution, balance in ACCOUNTS:
            # Statement-import accounts have no live connection behind them.
            if provider != "csv" and institution not in connections:
                conn = Connection(provider=provider, institution_name=institution, status="linked")
                db.add(conn)
                db.commit()
                connections[institution] = conn

            raw = RawAccount(
                external_id=external_id,
                name=name,
                kind=kind,
                balance_minor=balance,
                institution_name=institution,
                meta={"holdings": _demo_holdings()} if provider == "trading212" else {},
            )
            account = upsert_account(db, provider, raw, connections.get(institution))
            added = upsert_transactions(db, account, data.get(external_id, []))
            if verbose:
                print(f"  {name:<38} {added:>5} transactions")

        for name, category, kind, value in [
            ("Volkswagen Golf (2019)", "car", "asset", 9_850.00),
            ("Workplace pension (Aviva)", "pension", "asset", 23_400.00),
        ]:
            if not db.query(ManualAsset).filter_by(name=name).first():
                db.add(
                    ManualAsset(
                        name=name, category=category, kind=kind, value_minor=to_minor(value)
                    )
                )

        for category, limit in [
            ("Groceries", 400),
            ("Eating Out", 200),
            ("Shopping", 250),
            ("Transport", 120),
            ("Fuel", 150),
        ]:
            if not db.query(Budget).filter_by(category=category).first():
                db.add(Budget(category=category, monthly_limit_minor=to_minor(limit)))
        db.commit()

        pairs = detect_transfers(db)
        if verbose:
            print(f"  transfer pairs matched: {pairs}")

        _backfill_snapshots(db)
        snapshot_networth(db)
        if verbose:
            print("\nDemo data loaded. Start the API and open the dashboard.")
    finally:
        db.close()


def _demo_holdings() -> list[dict]:
    return [
        {"ticker": "VUAG", "quantity": 41.2, "average_price": 82.1, "current_price": 96.4, "value": 3971.68, "ppl": 589.16},
        {"ticker": "VWRP", "quantity": 24.0, "average_price": 104.0, "current_price": 118.7, "value": 2848.80, "ppl": 352.80},
        {"ticker": "AAPL", "quantity": 12.0, "average_price": 141.2, "current_price": 176.9, "value": 2122.80, "ppl": 428.40},
        {"ticker": "MSFT", "quantity": 5.5, "average_price": 310.0, "current_price": 388.2, "value": 2135.10, "ppl": 430.10},
    ]


def _backfill_snapshots(db) -> None:
    """A believable net-worth curve so the history chart isn't a single dot.

    Anchored to today's actual computed net worth and walked *backwards*, so the
    history joins up with the real figure instead of jumping at the last point.
    """
    from app.analytics.networth import compute_networth

    current = compute_networth(db)["_minor"]["net"]
    liabilities = compute_networth(db)["_minor"]["liabilities"]
    weekly_growth = 26_000  # ~£260/week saved

    for weeks_ago in range(1, 53):
        day = TODAY - dt.timedelta(weeks=weeks_ago)
        if db.query(NetWorthSnapshot).filter_by(taken_on=day).first():
            continue
        net = int(current - weeks_ago * weekly_growth + random.randint(-70_000, 70_000))
        db.add(
            NetWorthSnapshot(
                taken_on=day,
                assets_minor=net + liabilities,
                liabilities_minor=liabilities,
                net_minor=net,
                breakdown={},
            )
        )
    db.commit()


if __name__ == "__main__":
    main()
