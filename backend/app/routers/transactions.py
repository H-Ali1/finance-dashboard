"""Transactions, categorisation rules, budgets and statement imports."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Account, Budget, CategoryRule, Transaction
from ..money import to_major, to_minor
from ..providers import ProviderError, csv_import
from ..schemas import BudgetUpsert, MerchantCategory, RuleCreate, TransactionUpdate
from ..services.categorise import CATEGORIES, normalise_merchant
from ..services.sync import categorise_one, recategorise_all, upsert_transactions
from ..services.transfers import NOT_A_TRANSFER, detect_transfers

router = APIRouter(prefix="/api", tags=["transactions"])


def _serialise(txn: Transaction, accounts: dict[int, Account]) -> dict:
    account = accounts.get(txn.account_id)
    return {
        "id": txn.id,
        "date": txn.booked_on.isoformat(),
        "amount": to_major(txn.amount_minor),
        "currency": txn.currency,
        "merchant": txn.merchant,
        "merchant_key": txn.merchant_key,
        "description": txn.description,
        "category": txn.category,
        "category_source": txn.category_source,
        "status": txn.status,
        "is_transfer": txn.is_transfer,
        "excluded": txn.excluded,
        "notes": txn.notes,
        "account": account.name if account else "",
        "account_id": txn.account_id,
        "institution": account.institution_name if account else "",
    }


@router.get("/transactions")
def list_transactions(
    start: dt.date | None = None,
    end: dt.date | None = None,
    category: str | None = None,
    account_id: int | None = None,
    search: str | None = None,
    include_transfers: bool = True,
    min_amount: float | None = None,
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(Transaction)
    if start:
        stmt = stmt.where(Transaction.booked_on >= start)
    if end:
        stmt = stmt.where(Transaction.booked_on <= end)
    if category:
        stmt = stmt.where(Transaction.category == category)
    if account_id:
        stmt = stmt.where(Transaction.account_id == account_id)
    if not include_transfers:
        stmt = stmt.where(Transaction.is_transfer.is_(False))
    if min_amount is not None:
        stmt = stmt.where(func.abs(Transaction.amount_minor) >= to_minor(min_amount))
    if search:
        needle = f"%{search.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Transaction.merchant).like(needle),
                func.lower(Transaction.description).like(needle),
            )
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        db.scalars(
            stmt.order_by(Transaction.booked_on.desc(), Transaction.id.desc()).limit(min(limit, 1000)).offset(offset)
        )
    )
    accounts = {a.id: a for a in db.scalars(select(Account))}
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "transactions": [_serialise(t, accounts) for t in rows],
    }


@router.patch("/transactions/{transaction_id}")
def update_transaction(transaction_id: int, payload: TransactionUpdate, db: Session = Depends(get_db)) -> dict:
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        raise HTTPException(status_code=404, detail="Transaction not found")

    if payload.category is not None:
        txn.category = payload.category
        txn.category_source = "manual"
    if payload.merchant is not None:
        txn.merchant = payload.merchant
        txn.merchant_key = normalise_merchant(payload.merchant)
    if payload.notes is not None:
        txn.notes = payload.notes
    if payload.excluded is not None:
        txn.excluded = payload.excluded
    if payload.is_transfer is not None and payload.is_transfer != txn.is_transfer:
        txn.is_transfer = payload.is_transfer
        # Your say is final: never paired again, or kept out of the totals for good.
        txn.transfer_group = None if payload.is_transfer else NOT_A_TRANSFER
        if txn.category_source != "manual":
            txn.category = "Transfers" if payload.is_transfer else categorise_one(db, txn)
    db.commit()
    accounts = {a.id: a for a in db.scalars(select(Account))}
    return _serialise(txn, accounts)


@router.get("/categories")
def categories(db: Session = Depends(get_db)) -> dict:
    used = {row[0] for row in db.execute(select(Transaction.category).distinct())}
    return {"categories": sorted(set(CATEGORIES) | used)}


# --- rules -------------------------------------------------------------------
@router.get("/rules")
def list_rules(db: Session = Depends(get_db)) -> dict:
    rules = db.scalars(select(CategoryRule).order_by(CategoryRule.priority, CategoryRule.id))
    return {
        "rules": [
            {
                "id": r.id,
                "pattern": r.pattern,
                "match_type": r.match_type,
                "field": r.field,
                "category": r.category,
                "priority": r.priority,
            }
            for r in rules
        ]
    }


def save_rule(
    db: Session, pattern: str, match_type: str, field: str, category: str, priority: int | None = None
) -> CategoryRule:
    """Save a rule, or change the one already saved for this pattern. The
    database allows one per pattern; if a save at the same moment got there
    first, that one is changed instead."""
    where = (CategoryRule.pattern == pattern, CategoryRule.match_type == match_type, CategoryRule.field == field)
    for _ in range(2):
        rule = db.scalar(select(CategoryRule).where(*where))
        if rule is None:
            rule = CategoryRule(pattern=pattern, match_type=match_type, field=field)
            db.add(rule)
        rule.category = category
        if priority is not None:
            rule.priority = priority
        try:
            db.commit()
            return rule
        except IntegrityError:
            db.rollback()
    raise HTTPException(status_code=409, detail="That rule was being saved at the same time; try again")


@router.post("/rules")
def create_rule(payload: RuleCreate, db: Session = Depends(get_db)) -> dict:
    pattern = payload.pattern.strip()
    # A regex keeps its case: lower-casing would turn \S into \s.
    pattern = pattern if payload.match_type == "regex" else pattern.lower()
    rule = save_rule(db, pattern, payload.match_type, payload.field, payload.category, payload.priority)
    changed = recategorise_all(db) if payload.apply_now else 0
    return {"id": rule.id, "recategorised": changed}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db)) -> dict:
    rule = db.get(CategoryRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    db.delete(rule)
    db.commit()
    return {"deleted": rule_id, "recategorised": recategorise_all(db)}


# --- quick categorising ------------------------------------------------------
@router.get("/categorise/uncategorised")
def uncategorised_merchants(days: int = 180, limit: int = 40, db: Session = Depends(get_db)) -> dict:
    """Uncategorised spending grouped by merchant, biggest first: the list to
    work down, one merchant (and every future payment to it) at a time."""
    since = dt.date.today() - dt.timedelta(days=max(days, 1))
    rows = db.scalars(
        select(Transaction)
        .where(
            Transaction.category == "Uncategorised",
            Transaction.booked_on >= since,
            Transaction.is_transfer.is_(False),
            Transaction.excluded.is_(False),
            Transaction.amount_minor < 0,
        )
        .order_by(Transaction.booked_on.desc())
    )
    accounts = {a.id: a for a in db.scalars(select(Account))}
    groups: dict[str, dict] = {}
    for txn in rows:
        key = txn.merchant_key or normalise_merchant(txn.merchant) or txn.merchant.lower()
        group = groups.get(key)
        if group is None:  # newest first, so this is the latest payment
            account = accounts.get(txn.account_id)
            group = groups[key] = {
                "merchant": txn.merchant,
                "merchant_key": key,
                "example": txn.description,
                "last_date": txn.booked_on.isoformat(),
                "account": account.name if account else "",
                "transactions": 0,
                "total_minor": 0,
            }
        group["transactions"] += 1
        group["total_minor"] -= txn.amount_minor

    ranked = sorted(groups.values(), key=lambda g: g["total_minor"], reverse=True)
    total = sum(g["total_minor"] for g in ranked)
    return {
        "days": days,
        "total": to_major(total),
        "transactions": sum(g["transactions"] for g in ranked),
        "merchant_count": len(ranked),
        "merchants": [{**g, "total": to_major(g.pop("total_minor"))} for g in ranked[: max(limit, 0)]],
    }


@router.post("/categorise/merchant")
def categorise_merchant(payload: MerchantCategory, db: Session = Depends(get_db)) -> dict:
    """Put a merchant in a category, now and from now on: saves a rule on its
    name and re-files its past transactions (ones you set by hand stay)."""
    key = (payload.merchant_key or "").strip().lower() or (
        normalise_merchant(payload.merchant) or payload.merchant.strip().lower()
    )
    category = payload.category.strip()
    # Whole words, so the rule follows the payee when a reference is added to
    # the name, without catching other merchants that contain it.
    match_type = "words"
    # Strictly ahead of any other rule on this exact name, so your choice wins.
    others = db.scalar(
        select(func.min(CategoryRule.priority)).where(
            CategoryRule.pattern == key,
            or_(CategoryRule.field != "merchant", CategoryRule.match_type != match_type),
        )
    )
    rule = save_rule(db, key, match_type, "merchant", category, 100 if others is None else min(others - 1, 100))
    return {"rule_id": rule.id, "pattern": key, "category": category, "recategorised": recategorise_all(db)}


@router.post("/recategorise")
def recategorise(include_manual: bool = False, db: Session = Depends(get_db)) -> dict:
    return {"changed": recategorise_all(db, include_manual=include_manual)}


@router.post("/transfers/detect")
def redetect_transfers(db: Session = Depends(get_db)) -> dict:
    return {"pairs": detect_transfers(db)}


# --- budgets -----------------------------------------------------------------
@router.get("/budgets")
def list_budgets(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(Budget).order_by(Budget.category))
    return {
        "budgets": [
            {"id": b.id, "category": b.category, "monthly_limit": to_major(b.monthly_limit_minor)} for b in rows
        ]
    }


@router.put("/budgets")
def upsert_budget(payload: BudgetUpsert, db: Session = Depends(get_db)) -> dict:
    budget = db.scalar(select(Budget).where(Budget.category == payload.category))
    if budget is None:
        budget = Budget(category=payload.category, monthly_limit_minor=0)
        db.add(budget)
    budget.monthly_limit_minor = to_minor(payload.monthly_limit)
    db.commit()
    return {"category": budget.category, "monthly_limit": to_major(budget.monthly_limit_minor)}


@router.delete("/budgets/{category}")
def delete_budget(category: str, db: Session = Depends(get_db)) -> dict:
    budget = db.scalar(select(Budget).where(Budget.category == category))
    if budget is None:
        raise HTTPException(status_code=404, detail="No budget for that category")
    db.delete(budget)
    db.commit()
    return {"deleted": category}


# --- statement import --------------------------------------------------------
@router.post("/accounts/manual")
def create_manual_account(
    name: str = Form(...),
    institution: str = Form(""),
    kind: str = Form("current"),
    db: Session = Depends(get_db),
) -> dict:
    external_id = f"manual:{name.lower().replace(' ', '-')}"
    existing = db.scalar(select(Account).where(Account.provider == "csv", Account.external_id == external_id))
    if existing:
        return {"id": existing.id, "name": existing.name, "created": False}

    account = Account(
        provider="csv",
        external_id=external_id,
        name=name,
        institution_name=institution or name,
        kind=kind,
    )
    db.add(account)
    db.commit()
    return {"id": account.id, "name": account.name, "created": True}


@router.post("/import/csv")
async def import_csv(
    account_id: int = Form(...),
    file: UploadFile = File(...),
    fmt: str | None = Form(None),
    flip_sign: bool = Form(False),
    closing_balance: float | None = Form(None),
    db: Session = Depends(get_db),
) -> dict:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    content = await file.read()
    try:
        parsed = csv_import.parse_statement(content, flip_sign=flip_sign)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rows = parsed.rows

    # A card statement lists purchases; if they arrive as positive numbers
    # (money in, by our convention) the file uses the card issuer's convention.
    auto_flipped = False
    if account.kind == "credit_card" and not parsed.flipped:
        positive = sum(1 for r in rows if r.amount_minor > 0)
        if positive > len(rows) / 2:
            for r in rows:
                r.amount_minor = -r.amount_minor
            auto_flipped = True

    added = upsert_transactions(db, account, rows)
    pairs = detect_transfers(db)
    recategorise_all(db)

    # Statement files carry a running balance we deliberately don't parse (the
    # column is inconsistent across banks); the closing balance is supplied by
    # the user instead so net worth stays right for imported accounts.
    if closing_balance is not None:
        account.balance_minor = to_minor(closing_balance)
        account.balance_updated_at = dt.datetime.now(dt.timezone.utc)
        db.commit()

    money_out = sum(-r.amount_minor for r in rows if r.amount_minor < 0)
    money_in = sum(r.amount_minor for r in rows if r.amount_minor > 0)
    return {
        "file": file.filename,
        "detected_format": parsed.detected_format,
        "rows_parsed": len(rows),
        "added": added,
        "duplicates_skipped": len(rows) - added,
        "first_date": min(r.booked_on for r in rows).isoformat(),
        "last_date": max(r.booked_on for r in rows).isoformat(),
        "money_in": to_major(money_in),
        "money_out": to_major(money_out),
        "signs_flipped": parsed.flipped or auto_flipped,
        "transfer_pairs": pairs,
        "account": account.name,
    }
