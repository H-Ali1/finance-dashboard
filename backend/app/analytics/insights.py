"""The "what needs fixing" engine.

Every rule returns a plain-English finding with, wherever possible, a pound
figure attached — an insight you can't act on or size isn't worth showing. Each
has a stable `key` so it can be dismissed and stay dismissed.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Connection, DismissedInsight, Transaction
from ..money import to_major
from ..services import backup
from . import anomalies as anomalies_mod
from . import cashflow as cashflow_mod
from . import recurring as recurring_mod
from . import spending as spending_mod
from .common import is_spend, load_transactions, month_key, per_request
from .networth import compute_networth

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "good": 3}


def _insight(key, severity, title, detail, *, impact=None, impact_period="year", action=None, area="general"):
    return {
        "key": key,
        "severity": severity,
        "title": title,
        "detail": detail,
        "impact": impact,
        "impact_period": impact_period,
        "action": action,
        "area": area,
    }


@per_request
def build_insights(db: Session) -> list[dict]:
    findings: list[dict] = []
    flow = cashflow_mod.cashflow(db)
    nw = compute_networth(db)
    budgets = spending_mod.budget_status(db)
    trends = spending_mod.category_trend(db, months=4)
    subs = recurring_mod.detect_recurring(db)
    subs_summary = recurring_mod.summary(subs)

    findings += _cashflow_rules(flow, nw)
    findings += _budget_rules(budgets)
    findings += _trend_rules(trends)
    findings += _subscription_rules(subs_summary, flow)
    findings += _debt_and_fee_rules(db, nw, flow)
    findings += _anomaly_rules(db)
    findings += _hygiene_rules(db)

    dismissed = {row.key for row in db.scalars(select(DismissedInsight))}
    findings = [f for f in findings if f["key"] not in dismissed]
    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), -(f["impact"] or 0)))
    return findings


# --- rule groups -------------------------------------------------------------
def _cashflow_rules(flow: dict, nw: dict) -> list[dict]:
    out = []
    projected_net = flow["projected_net"]
    if projected_net < 0:
        out.append(
            _insight(
                "cashflow:negative-month",
                "critical",
                f"You're on track to spend £{abs(projected_net):,.0f} more than you earn this month",
                f"Projected spend £{flow['projected_spend']:,.0f} against £{flow['projected_income']:,.0f} "
                f"of income, at £{flow['daily_spend_rate']:,.0f}/day so far.",
                impact=abs(projected_net),
                impact_period="month",
                area="cashflow",
            )
        )

    rate = flow["savings_rate"]
    if rate is not None:
        if rate < 0:
            out.append(
                _insight(
                    "cashflow:negative-savings-rate",
                    "critical",
                    "Your average month is running at a loss",
                    f"Over the last few complete months you spent £{flow['typical_spend']:,.0f} against "
                    f"£{flow['typical_income']:,.0f} income.",
                    impact=round((flow["typical_spend"] - flow["typical_income"]) * 12, 2),
                    area="cashflow",
                )
            )
        elif rate < 10:
            out.append(
                _insight(
                    "cashflow:low-savings-rate",
                    "warning",
                    f"Savings rate is {rate:.0f}% — below the 20% rule of thumb",
                    f"You keep about £{flow['typical_income'] - flow['typical_spend']:,.0f} of "
                    f"£{flow['typical_income']:,.0f} a month. Getting to 20% would mean finding "
                    f"£{flow['typical_income'] * 0.2 - (flow['typical_income'] - flow['typical_spend']):,.0f} a month.",
                    impact=round(
                        (flow["typical_income"] * 0.2 - (flow["typical_income"] - flow["typical_spend"])) * 12, 2
                    ),
                    area="cashflow",
                )
            )
        elif rate >= 20:
            out.append(
                _insight(
                    "cashflow:good-savings-rate",
                    "good",
                    f"Savings rate is {rate:.0f}%",
                    f"You're keeping roughly £{flow['typical_income'] - flow['typical_spend']:,.0f} a month.",
                    area="cashflow",
                )
            )

    runway = flow["runway_months"]
    if runway is not None:
        if runway < 1:
            out.append(
                _insight(
                    "cashflow:no-buffer",
                    "critical",
                    "Less than a month of essential spending in cash",
                    f"£{flow['liquid']:,.0f} liquid against £{flow['typical_essential']:,.0f} of "
                    "essential monthly costs. An unexpected bill would go on credit.",
                    action="Build a starter buffer of one month's essentials before anything else.",
                    area="cashflow",
                )
            )
        elif runway < 3:
            out.append(
                _insight(
                    "cashflow:thin-buffer",
                    "warning",
                    f"Emergency fund covers {runway:.1f} months",
                    f"£{flow['liquid']:,.0f} liquid vs £{flow['typical_essential']:,.0f}/month essentials. "
                    "Three to six months is the usual target.",
                    impact=round(max(0.0, (3 - runway)) * flow["typical_essential"], 2),
                    impact_period="total",
                    area="cashflow",
                )
            )
        elif runway > 12 and nw["investments"] < nw["liquid"]:
            out.append(
                _insight(
                    "cashflow:idle-cash",
                    "info",
                    f"{runway:.0f} months of expenses sitting in cash",
                    f"£{flow['liquid']:,.0f} is more buffer than you need. Cash beyond ~6 months "
                    "of essentials loses value to inflation.",
                    impact=round(max(0.0, flow["liquid"] - flow["typical_essential"] * 6) * 0.03, 2),
                    action="Consider moving the excess into your ISA or a fixed-rate saver.",
                    area="cashflow",
                )
            )
    return out


def _budget_rules(budgets: dict) -> list[dict]:
    out = []
    for row in budgets["budgets"]:
        if row["limit"] <= 0:
            continue
        over = row["projected"] - row["limit"]
        if row["spent"] > row["limit"]:
            out.append(
                _insight(
                    f"budget:blown:{row['category']}:{budgets['month']}",
                    "warning",
                    f"{row['category']} is over budget with "
                    f"{budgets['days_in_month'] - budgets['days_elapsed']} days left",
                    f"£{row['spent']:,.0f} spent against a £{row['limit']:,.0f} limit.",
                    impact=round(row["spent"] - row["limit"], 2),
                    impact_period="month",
                    area="budget",
                )
            )
        elif over > 0 and budgets["days_elapsed"] >= 7:
            out.append(
                _insight(
                    f"budget:pace:{row['category']}:{budgets['month']}",
                    "info",
                    f"{row['category']} is pacing £{over:,.0f} over budget",
                    f"£{row['spent']:,.0f} of £{row['limit']:,.0f} used with "
                    f"{budgets['days_in_month'] - budgets['days_elapsed']} days to go.",
                    impact=round(over, 2),
                    impact_period="month",
                    area="budget",
                )
            )

    if not budgets["budgets"] and budgets["unbudgeted"]:
        top = budgets["unbudgeted"][0]
        out.append(
            _insight(
                "budget:none-set",
                "info",
                "No budgets set yet",
                f"Your biggest category this month is {top['category']} at £{top['spent']:,.0f}. "
                "Setting a limit is what turns tracking into control.",
                action="Add budgets on the Spending tab.",
                area="budget",
            )
        )
    return out


def _trend_rules(trends: dict) -> list[dict]:
    out = []
    # In the first week there isn't enough of the month to call a trend, even
    # against a like-for-like month-to-date baseline.
    if (trends.get("through_day") or 31) < 7:
        return out
    for row in trends["categories"]:
        change = row["change"]
        pct = row["change_pct"]
        if pct is None or row["average"] < 20:
            continue
        if pct >= 25 and change >= 40:
            out.append(
                _insight(
                    f"trend:up:{row['category']}:{trends['current_month']}",
                    "warning",
                    f"{row['category']} is up {pct:.0f}% on your recent average",
                    f"£{row['current']:,.0f} this month vs £{row['average']:,.0f} typical — £{change:,.0f} more.",
                    impact=round(change * 12, 2),
                    area="trend",
                )
            )
        elif pct <= -25 and change <= -40:
            out.append(
                _insight(
                    f"trend:down:{row['category']}:{trends['current_month']}",
                    "good",
                    f"{row['category']} is down {abs(pct):.0f}%",
                    f"£{row['current']:,.0f} this month vs £{row['average']:,.0f} typical.",
                    area="trend",
                )
            )
    return out


def _subscription_rules(summary: dict, flow: dict) -> list[dict]:
    out = []
    for item in summary["price_rises"]:
        change = item["price_change"]
        out.append(
            _insight(
                f"subs:price-rise:{item['merchant_key']}",
                "warning",
                f"{item['merchant']} went up {change['pct']:.0f}% (£{change['from']:.2f} → £{change['to']:.2f})",
                f"Billed {item['cadence']}. At the new price that's £{item['annual_cost']:,.0f} a year.",
                impact=change["annual_impact"],
                action="Check whether a cheaper tier or an annual plan is available.",
                area="subscriptions",
            )
        )

    # Both halves of a duplicate are in the list; report the pair once.
    reported_families: set[str] = set()
    for item in summary["duplicates"]:
        if item["family"] in reported_families:
            continue
        reported_families.add(item["family"])
        out.append(
            _insight(
                f"subs:duplicate:{item['family']}",
                "warning",
                f"{item['merchant']} looks like it's being billed twice",
                f"Charged on {item['account']} and also on {', '.join(item['duplicate_of'])}.",
                impact=item["annual_cost"],
                action="Cancel whichever one you don't use.",
                area="subscriptions",
            )
        )

    for item in summary["stopped"]:
        if item["annual_cost"] < 60:
            continue
        out.append(
            _insight(
                f"subs:stopped:{item['merchant_key']}",
                "info",
                f"{item['merchant']} hasn't charged you since {item['last_seen']}",
                f"Was £{item['typical_amount']:.2f} {item['cadence']}. Either you cancelled it "
                "(nothing to do) or a payment failed.",
                area="subscriptions",
            )
        )

    # Deliberately the cancellable subset — rent and the water bill aren't
    # "subscriptions" you can review away, and including them buries the signal.
    monthly = summary["subscription_monthly"]
    income = flow["typical_income"]
    if income and monthly / income > 0.08:
        out.append(
            _insight(
                "subs:high-share",
                "warning",
                f"Subscriptions eat {monthly / income * 100:.0f}% of your income",
                f"{summary['subscription_count']} cancellable subscriptions costing £{monthly:,.0f}/month "
                f"(£{summary['subscription_annual']:,.0f}/year), on top of £{summary['bills_monthly']:,.0f} "
                "of fixed bills.",
                impact=summary["subscription_annual"],
                action="Review the Subscriptions tab and cut the bottom three.",
                area="subscriptions",
            )
        )
    return out


def _debt_and_fee_rules(db: Session, nw: dict, flow: dict) -> list[dict]:
    out = []
    card_debt = nw["card_debt"]
    if card_debt > 0:
        # Roughly what that balance costs at a typical UK card APR if carried.
        annual_interest = round(card_debt * 0.24, 2)
        severity = "critical" if card_debt > flow["typical_income"] else "warning"
        out.append(
            _insight(
                "debt:card-balance",
                severity,
                f"£{card_debt:,.0f} outstanding on cards",
                "Carried rather than cleared, a balance this size costs roughly "
                f"£{annual_interest:,.0f} a year at a typical 24% APR.",
                impact=annual_interest,
                action="Clear the highest-APR card first, or move it to a 0% balance transfer.",
                area="debt",
            )
        )

    since = dt.date.today() - dt.timedelta(days=90)
    fees = sum(
        -t.amount_minor for t in load_transactions(db, start=since) if is_spend(t) and t.category == "Fees & Interest"
    )
    if fees > 2500:  # £25 over three months
        out.append(
            _insight(
                "fees:paying-fees",
                "warning",
                f"£{to_major(fees):,.0f} in fees and interest over the last 3 months",
                "Overdraft interest, card interest and foreign-transaction fees are pure loss.",
                impact=round(to_major(fees) * 4, 2),
                action="A fee-free card for spending abroad and clearing the overdraft removes most of this.",
                area="fees",
            )
        )

    overdrawn = [a for a in nw["liability_items"] if a["kind"] in {"current", "savings"}]
    for account in overdrawn:
        out.append(
            _insight(
                f"debt:overdrawn:{account['id']}",
                "warning",
                f"{account['name']} is overdrawn by £{account['value']:,.0f}",
                "Arranged overdrafts in the UK typically run near 40% EAR — more expensive than a credit card.",
                impact=round(account["value"] * 0.4, 2),
                area="debt",
            )
        )
    return out


def _anomaly_rules(db: Session) -> list[dict]:
    out = []
    doubles = anomalies_mod.detect_double_charges(db)
    # A double charge is already reported as one finding; don't also report each
    # half of it as a surprisingly large transaction.
    covered = {(d["merchant"], d["amount"]) for d in doubles}

    for dupe in doubles[:5]:
        out.append(
            _insight(
                f"anomaly:double:{dupe['merchant']}:{dupe['dates'][0]}",
                "warning",
                f"Possible double charge: {dupe['merchant']} £{dupe['amount']:,.2f} twice",
                f"On {dupe['dates'][0]} and {dupe['dates'][1]} from {dupe['account']}.",
                impact=dupe["amount"],
                impact_period="one-off",
                action="Worth checking the receipt — merchants do refund these.",
                area="anomalies",
            )
        )

    flagged = [
        a
        for a in anomalies_mod.detect_anomalies(db)
        if a["severity"] == "warning" and (a["merchant"], a["amount"]) not in covered
    ][:5]
    for item in flagged:
        out.append(
            _insight(
                f"anomaly:outlier:{item['id']}",
                "info",
                f"Unusual: £{item['amount']:,.0f} at {item['merchant']}",
                "; ".join(item["reasons"]).capitalize() + f" ({item['date']}).",
                impact=item["amount"],
                impact_period="one-off",
                area="anomalies",
            )
        )
    return out


def _consent_rules(connection: Connection) -> list[dict]:
    """TrueLayer banks share data for 90 days, then need connecting again."""
    name = connection.institution_name
    expires = connection.expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=dt.timezone.utc)
    stamp = expires.date().isoformat() if expires else "unknown"
    reconnect = "Connect it again on the Connections tab. It takes a minute, and nothing is duplicated."
    if connection.status == "expired":
        return [
            _insight(
                f"connection:expired:{connection.id}:{stamp}",
                "warning",
                f"{name} needs connecting again",
                "Its 90 days of access through TrueLayer have ended, so new transactions aren't coming in.",
                action=reconnect,
                area="connections",
            )
        ]
    if expires is None:
        return []
    days_left = (expires - dt.datetime.now(dt.timezone.utc)).days
    if days_left > 10:
        return []
    return [
        _insight(
            f"connection:expiring:{connection.id}:{stamp}",
            "warning" if days_left <= 3 else "info",
            f"{name} stops syncing in {max(days_left, 0)} day{'s' if days_left != 1 else ''}",
            f"Its 90 days of access through TrueLayer end on {expires.strftime('%d %b')}.",
            action=reconnect,
            area="connections",
        )
    ]


def _hygiene_rules(db: Session) -> list[dict]:
    out = []
    for dup in anomalies_mod.detect_duplicate_accounts(db):
        first, second = dup["accounts"]
        out.append(
            _insight(
                f"accounts:duplicate:{dup['account_ids'][0]}:{dup['account_ids'][1]}",
                "warning",
                f"{first} and {second} look like the same account",
                f"{dup['shared']} of {dup['of']} transactions match, so that spending is counted twice.",
                action="Untick 'Counted' for the one you don't use, under Accounts on the Net worth tab.",
                area="hygiene",
            )
        )
    for connection in db.scalars(select(Connection)):
        if connection.provider == "truelayer":
            out.extend(_consent_rules(connection))
        if connection.status == "error":
            out.append(
                _insight(
                    f"connection:error:{connection.id}",
                    "warning",
                    f"{connection.institution_name} isn't syncing",
                    connection.last_error or "The last sync failed.",
                    action="Reconnect it on the Connections tab.",
                    area="connections",
                )
            )

    notice = backup.restore_notice()
    if notice:
        source = notice.get("restored_from")
        out.append(
            _insight(
                f"maintenance:restored:{notice.get('at')}",
                "warning",
                "The database was restored from a backup"
                if source
                else "The database was damaged and started again empty",
                (
                    f"On {str(notice.get('at', ''))[:10]} the database file was damaged "
                    f"({notice.get('problem')}), so it was replaced with {source}. "
                    "Bank feeds catch up on their own at the next sync."
                    if source
                    else f"On {str(notice.get('at', ''))[:10]} the database file was damaged "
                    f"({notice.get('problem')}) and no good backup existed."
                )
                + f" The damaged file was kept as {notice.get('damaged_copy')}.",
                action="Re-import any statement files from after the backup date.",
                area="hygiene",
            )
        )

    total = db.scalar(select(Transaction).where(Transaction.id.is_not(None)).limit(1))
    if total is not None:
        uncategorised = spending_mod.uncategorised_count(db)
        recent = load_transactions(db, start=dt.date.today() - dt.timedelta(days=90))
        if recent and uncategorised / max(len(recent), 1) > 0.1:
            out.append(
                _insight(
                    "hygiene:uncategorised",
                    "info",
                    f"{uncategorised} transactions are uncategorised",
                    "Category totals and budgets will under-report until these are sorted.",
                    action="Categorise them on the Transactions tab — one rule usually fixes dozens.",
                    area="hygiene",
                )
            )
    return out


def headline(db: Session) -> dict:
    """Single-sentence state of play for the top of the dashboard."""
    flow = cashflow_mod.cashflow(db)
    nw = compute_networth(db)
    findings = build_insights(db)
    critical = [f for f in findings if f["severity"] == "critical"]
    warnings = [f for f in findings if f["severity"] == "warning"]

    if critical:
        status, message = "bad", critical[0]["title"]
    elif len(warnings) >= 3:
        status, message = "watch", f"{len(warnings)} things worth a look this month"
    elif flow["projected_net"] > 0:
        status, message = "good", f"On track to keep £{flow['projected_net']:,.0f} this month"
    else:
        status, message = "watch", "Roughly breaking even this month"

    return {
        "status": status,
        "message": message,
        "month": month_key(dt.date.today()),
        "net_worth": nw["net"],
        "projected_net": flow["projected_net"],
        "counts": {
            "critical": len(critical),
            "warning": len(warnings),
            "info": len([f for f in findings if f["severity"] == "info"]),
            "good": len([f for f in findings if f["severity"] == "good"]),
        },
        "essential_categories": settings.essential_category_list,
    }
