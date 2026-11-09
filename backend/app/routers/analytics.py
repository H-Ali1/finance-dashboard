"""Everything the dashboard reads."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analytics import anomalies as anomalies_mod
from ..analytics import cashflow as cashflow_mod
from ..analytics import income as income_mod
from ..analytics import insights as insights_mod
from ..analytics import recurring as recurring_mod
from ..analytics import spending as spending_mod
from ..analytics.common import month_bounds, month_key
from ..analytics.networth import compute_networth, networth_history
from ..db import get_db
from ..models import DismissedInsight
from ..schemas import DismissRequest

router = APIRouter(prefix="/api", tags=["analytics"])


def _range(start: dt.date | None, end: dt.date | None, month: str | None) -> tuple[dt.date, dt.date]:
    if month:
        return month_bounds(month)
    if start and end:
        return start, end
    return month_bounds(month_key(dt.date.today()))


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)) -> dict:
    start, end = month_bounds(month_key(dt.date.today()))
    subs = recurring_mod.detect_recurring(db)
    nw = compute_networth(db)
    nw.pop("_minor", None)
    flow = cashflow_mod.cashflow(db)
    flow.pop("_minor", None)

    return {
        "headline": insights_mod.headline(db),
        "networth": nw,
        "networth_history": networth_history(db, 365)["series"][-90:],
        "cashflow": flow,
        "spending": spending_mod.category_breakdown(db, start, end),
        "trend": spending_mod.monthly_trend(db, 12),
        "budgets": spending_mod.budget_status(db),
        "subscriptions": recurring_mod.summary(subs),
        "upcoming": recurring_mod.upcoming(subs, 30)[:8],
        "insights": insights_mod.build_insights(db)[:8],
        "top_merchants": spending_mod.top_merchants(db, start, end, 8)["merchants"],
    }


@router.get("/spending/categories")
def spending_categories(
    start: dt.date | None = None,
    end: dt.date | None = None,
    month: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    return spending_mod.category_breakdown(db, *_range(start, end, month))


@router.get("/spending/trend")
def spending_trend(months: int = 12, db: Session = Depends(get_db)) -> dict:
    return spending_mod.monthly_trend(db, months)


@router.get("/spending/by-category-trend")
def spending_category_trend(months: int = 6, limit: int = 8, db: Session = Depends(get_db)) -> dict:
    return spending_mod.category_trend(db, months, limit)


@router.get("/spending/merchants")
def spending_merchants(
    start: dt.date | None = None,
    end: dt.date | None = None,
    month: str | None = None,
    limit: int = 20,
    db: Session = Depends(get_db),
) -> dict:
    range_start, range_end = _range(start, end, month)
    return spending_mod.top_merchants(db, range_start, range_end, limit)


@router.get("/spending/accounts")
def spending_accounts(
    start: dt.date | None = None,
    end: dt.date | None = None,
    month: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    return spending_mod.account_spend(db, *_range(start, end, month))


@router.get("/spending/budgets")
def spending_budgets(month: str | None = None, db: Session = Depends(get_db)) -> dict:
    return spending_mod.budget_status(db, month)


@router.get("/subscriptions")
def subscriptions(db: Session = Depends(get_db)) -> dict:
    items = recurring_mod.detect_recurring(db)
    return {
        "summary": recurring_mod.summary(items),
        "items": items,
        "upcoming": recurring_mod.upcoming(items, 30),
    }


@router.get("/cashflow")
def cashflow(months: int = 6, db: Session = Depends(get_db)) -> dict:
    data = cashflow_mod.cashflow(db, months)
    data.pop("_minor", None)
    data["burn"] = cashflow_mod.daily_burn(db, 60)["series"]
    return data


@router.get("/income")
def income(months: int = 12, db: Session = Depends(get_db)) -> dict:
    """Salary, tutoring and other income by month: how much, how regular, the trend."""
    return income_mod.income_breakdown(db, min(max(months, 1), 36))


@router.get("/anomalies")
def anomalies(days: int = 45, db: Session = Depends(get_db)) -> dict:
    return {
        "outliers": anomalies_mod.detect_anomalies(db, days),
        "double_charges": anomalies_mod.detect_double_charges(db),
    }


@router.get("/insights")
def insights(db: Session = Depends(get_db)) -> dict:
    findings = insights_mod.build_insights(db)
    return {
        "headline": insights_mod.headline(db),
        "insights": findings,
        "by_area": {area: [f for f in findings if f["area"] == area] for area in sorted({f["area"] for f in findings})},
        "total_annual_impact": round(sum(f["impact"] or 0 for f in findings if f["impact_period"] == "year"), 2),
    }


@router.post("/insights/dismiss")
def dismiss_insight(payload: DismissRequest, db: Session = Depends(get_db)) -> dict:
    existing = db.scalar(select(DismissedInsight).where(DismissedInsight.key == payload.key))
    if existing is None:
        db.add(DismissedInsight(key=payload.key))
        db.commit()
    return {"dismissed": payload.key}


@router.delete("/insights/dismiss/{key:path}")
def undismiss_insight(key: str, db: Session = Depends(get_db)) -> dict:
    existing = db.scalar(select(DismissedInsight).where(DismissedInsight.key == key))
    if existing is not None:
        db.delete(existing)
        db.commit()
    return {"restored": key}
