"""Transactions, categorisation rules, budgets and statement imports."""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Account, Budget, CategoryRule, Transaction, rule_signature
from ..money import to_major, to_minor
from ..providers import ProviderError, csv_import
from ..schemas import BudgetUpsert, MerchantCategory, RuleCreate, RulePreview, TransactionUpdate
from ..services import learn
from ..services.categorise import (
    CATEGORIES,
    PAYMENT_TYPES,
    CompiledRule,
    categorise,
    compile_rules,
    normalise_merchant,
    rule_key,
)
from ..services.sync import (
    bank_key,
    categorise_one,
    recategorise_all,
    rule_outcome,
    rules_in_order,
    upsert_transactions,
)
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
        # Set when a rule or you renamed it: what the bank calls it.
        "bank_merchant": txn.bank_merchant if txn.merchant_source else None,
        "merchant_source": txn.merchant_source,
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
    source: str | None = None,
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
    if source:  # who set the category: auto, rule, manual or learned
        stmt = stmt.where(Transaction.category_source == source)
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
        name = payload.merchant.strip()
        if txn.merchant_source is None:  # keep the bank's name, to go back to
            txn.bank_merchant, txn.bank_merchant_key = txn.merchant, txn.merchant_key
        if name:
            txn.merchant, txn.merchant_key, txn.merchant_source = name, rule_key(name), "manual"
        else:  # an empty name: back to the bank's, and to your rules
            txn.merchant = txn.bank_merchant or txn.merchant
            txn.merchant_key = bank_key(txn)
            txn.bank_merchant = txn.bank_merchant_key = txn.merchant_source = None
    if payload.notes is not None:
        txn.notes = payload.notes
    if payload.excluded is not None:
        # Yours either way: a rule won't put it back in, or take it out again.
        txn.excluded, txn.excluded_source = payload.excluded, "manual"
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
def _rule_json(rule: CategoryRule) -> dict:
    return {
        "id": rule.id,
        "pattern": rule.pattern,
        "match_type": rule.match_type,
        "field": rule.field,
        "direction": rule.direction,
        "amount_min": None if rule.amount_min_minor is None else to_major(rule.amount_min_minor),
        "amount_max": None if rule.amount_max_minor is None else to_major(rule.amount_max_minor),
        "account_id": rule.account_id,
        "payment_type": rule.payment_type,
        "category": rule.category or None,
        "rename_to": rule.rename_to,
        "exclude": bool(rule.exclude),
        "priority": rule.priority,
    }


@router.get("/rules")
def list_rules(db: Session = Depends(get_db)) -> dict:
    """In the order they're checked: the most specific first."""
    return {"rules": [_rule_json(r) for r in rules_in_order(db)]}


def _rule_values(payload: RuleCreate, db: Session, *, need_action: bool = True) -> tuple[dict, dict]:
    """The rule's conditions and actions, checked and in stored form."""
    pattern = payload.pattern.strip()
    if payload.match_type == "regex":
        # A regex keeps its case: lower-casing would turn \S into \s.
        try:
            re.compile(pattern)
        except re.error as exc:
            raise HTTPException(status_code=400, detail=f"That isn't a valid regular expression: {exc}") from exc
    else:
        pattern = pattern.lower()
    low = to_minor(payload.amount_min) if payload.amount_min else None
    high = to_minor(payload.amount_max) if payload.amount_max else None
    if low is not None and high is not None and low > high:
        raise HTTPException(status_code=400, detail="The smallest amount is more than the largest")
    payment_type = payload.payment_type or None
    if payment_type and payment_type not in PAYMENT_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown kind of payment: {payment_type}")
    if payload.account_id is not None and db.get(Account, payload.account_id) is None:
        raise HTTPException(status_code=404, detail="Account not found")

    conditions = {
        "pattern": pattern,
        # Without text these say nothing; the defaults keep one rule per set of conditions.
        "match_type": payload.match_type if pattern else "contains",
        "field": payload.field if pattern else "merchant",
        "direction": payload.direction,
        "amount_min_minor": low,
        "amount_max_minor": high,
        "account_id": payload.account_id,
        "payment_type": payment_type,
    }
    if not (pattern or payload.direction or low or high or payload.account_id is not None or payment_type):
        raise HTTPException(status_code=400, detail="A rule needs at least one condition")
    actions = {
        "category": (payload.category or "").strip(),
        "rename_to": (payload.rename_to or "").strip() or None,
        "exclude": payload.exclude,
    }
    if need_action and not (actions["category"] or actions["rename_to"] or actions["exclude"]):
        raise HTTPException(
            status_code=400,
            detail="A rule needs something to do: a category, a new name, or leaving it out of the totals",
        )
    return conditions, actions


def save_rule(db: Session, conditions: dict, actions: dict, priority: int | None = None) -> CategoryRule:
    """Save a rule, or change the one already saved with these conditions.
    Only the actions given change. The database allows one rule per set of
    conditions; if a save at the same moment got there first, that one is
    changed instead."""
    signature = rule_signature(**conditions)
    for _ in range(2):
        rule = db.scalar(select(CategoryRule).where(CategoryRule.signature == signature))
        if rule is None:
            rule = CategoryRule(**conditions)
            db.add(rule)
        for name, value in actions.items():
            setattr(rule, name, value)
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
    conditions, actions = _rule_values(payload, db)
    rule = save_rule(db, conditions, actions, payload.priority)
    changed = recategorise_all(db) if payload.apply_now else 0
    return {"id": rule.id, "recategorised": changed}


@router.put("/rules/{rule_id}")
def update_rule(rule_id: int, payload: RuleCreate, db: Session = Depends(get_db)) -> dict:
    rule = db.get(CategoryRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    conditions, actions = _rule_values(payload, db)
    clash = db.scalar(
        select(CategoryRule.id).where(
            CategoryRule.signature == rule_signature(**conditions), CategoryRule.id != rule_id
        )
    )
    if clash is not None:
        raise HTTPException(status_code=409, detail="Another rule has exactly these conditions: change that one")
    for name, value in {**conditions, **actions}.items():
        setattr(rule, name, value)
    if "priority" in payload.model_fields_set:
        rule.priority = payload.priority
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Another rule has exactly these conditions") from exc
    changed = recategorise_all(db) if payload.apply_now else 0
    return {"id": rule.id, "recategorised": changed}


@router.post("/rules/preview")
def preview_rule(payload: RulePreview, db: Session = Depends(get_db)) -> dict:
    """What a rule would do, before it's saved: the transactions it matches,
    and how many of them it would change. Your own choices stay as they are.
    It works before an action is chosen, to show what the conditions catch."""
    conditions, actions = _rule_values(payload, db, need_action=False)
    existing = db.get(CategoryRule, payload.rule_id) if payload.rule_id is not None else None
    priority = payload.priority if existing is None or "priority" in payload.model_fields_set else existing.priority
    candidate = CompiledRule.of(CategoryRule(**conditions, **actions, priority=priority, id=payload.rule_id))
    rules = compile_rules([r for r in rules_in_order(db) if r.id != payload.rule_id] + [candidate])

    rows = db.scalars(
        select(Transaction)
        .where(Transaction.is_transfer.is_(False))
        .order_by(Transaction.booked_on.desc(), Transaction.id.desc())
    )
    accounts = {a.id: a for a in db.scalars(select(Account))}
    matches = changes = 0
    examples = []
    for txn in rows:
        keys = tuple({bank_key(txn), txn.merchant_key})
        if not candidate.matches(keys, txn.description, txn.amount_minor, txn.account_id, txn.payment_type):
            continue
        matches += 1
        after = _after_rules(txn, rules)
        changed = after != (txn.merchant, txn.category, txn.excluded)
        changes += changed
        if len(examples) < 8:
            account = accounts.get(txn.account_id)
            examples.append(
                {
                    "id": txn.id,
                    "date": txn.booked_on.isoformat(),
                    "amount": to_major(txn.amount_minor),
                    "description": txn.description,
                    "account": account.name if account else "",
                    "merchant": txn.merchant,
                    "category": txn.category,
                    "excluded": txn.excluded,
                    "new_merchant": after[0],
                    "new_category": after[1],
                    "new_excluded": after[2],
                    "changes": changed,
                }
            )
    return {"matches": matches, "changes": changes, "examples": examples}


def _after_rules(txn: Transaction, rules: list[CompiledRule]) -> tuple[str, str, bool]:
    """(merchant, category, excluded) once `rules` apply, without saving."""
    outcome = rule_outcome(txn, rules)
    merchant, merchant_key = txn.merchant, txn.merchant_key
    if txn.merchant_source != "manual":
        if outcome.rename:
            merchant, merchant_key = outcome.rename, rule_key(outcome.rename)
        elif txn.merchant_source == "rule":
            merchant, merchant_key = txn.bank_merchant or merchant, bank_key(txn)
    excluded = txn.excluded
    if txn.excluded_source != "manual":
        excluded = bool(outcome.exclude) or (txn.excluded and txn.excluded_source != "rule")

    category = txn.category
    if txn.category_source != "manual":
        if outcome.category:
            category = outcome.category
        elif txn.category_source == "rule":  # no rule files it any more
            category = categorise(
                merchant=merchant,
                description=txn.description,
                amount_minor=txn.amount_minor,
                mcc=txn.mcc,
                provider_category=txn.provider_category,
                payment_type=txn.payment_type,
                merchant_key=merchant_key,
                outcome=outcome,
            )[0]
    return merchant, category, excluded


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
    # All money out: what you've categorised, at any time, is what's learned from.
    money_out = list(
        db.scalars(
            select(Transaction)
            .where(Transaction.is_transfer.is_(False), Transaction.amount_minor < 0)
            .order_by(Transaction.booked_on.desc())
        )
    )
    learner = learn.Learner.from_rows(money_out)
    accounts = {a.id: a for a in db.scalars(select(Account))}

    def grouped(rows: list[Transaction]) -> tuple[list[dict], dict[str, list[Transaction]]]:
        groups: dict[str, dict] = {}
        members: dict[str, list[Transaction]] = defaultdict(list)
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
                    "category": txn.category,
                    "transactions": 0,
                    "total_minor": 0,
                }
            group["transactions"] += 1
            group["total_minor"] -= txn.amount_minor
            members[key].append(txn)
        return sorted(groups.values(), key=lambda g: g["total_minor"], reverse=True), members

    recent = [t for t in money_out if t.booked_on >= since and not t.excluded]
    ranked, members = grouped([t for t in recent if t.category == "Uncategorised"])
    for group in ranked:
        guess = learner.guess_for(members[group["merchant_key"]])
        group["suggestion"] = (
            {"category": guess.category, "probability": guess.probability, "because": guess.because}
            if guess and guess.suggests
            else None
        )
    # Filed by learning: listed to check, until you confirm or change them.
    learned, _ = grouped([t for t in recent if t.category_source == learn.LEARNED])

    def shown(groups: list[dict]) -> list[dict]:
        return [
            {**{k: v for k, v in g.items() if k != "total_minor"}, "total": to_major(g["total_minor"])}
            for g in groups[: max(limit, 0)]
        ]

    total = sum(g["total_minor"] for g in ranked)
    return {
        "days": days,
        "total": to_major(total),
        "transactions": sum(g["transactions"] for g in ranked),
        "merchant_count": len(ranked),
        "learned_count": len(learned),
        # How many merchants you've categorised, to learn from.
        "learned_from": len(learner.examples),
        "merchants": shown(ranked),
        "learned": shown(learned),
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
            func.lower(CategoryRule.pattern) == key,  # regex rules keep their case
            or_(CategoryRule.field != "merchant", CategoryRule.match_type != match_type),
        )
    )
    rule = save_rule(
        db,
        {"pattern": key, "match_type": match_type, "field": "merchant"},
        # Only the category: a new name or exclusion on the same rule stays.
        {"category": category},
        100 if others is None else min(others - 1, 100),
    )
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
