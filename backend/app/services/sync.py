"""Pull from every configured provider and fold the results into the database."""

from __future__ import annotations

import datetime as dt
import threading
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Account, CategoryRule, Connection, SyncLog, Transaction
from ..providers import ProviderError, RawAccount, RawTransaction, starling, trading212, truelayer
from . import income, learn
from .categorise import (
    CompiledRule,
    RuleOutcome,
    categorise,
    compile_rules,
    display_merchant,
    evaluate_rules,
    normalise_merchant,
    rule_key,
    rule_order,
)
from .transfers import NOT_A_TRANSFER, detect_transfers

DEFAULT_LOOKBACK_DAYS = 730
# Re-pull a few days behind the newest row: banks tweak pending rows after the fact.
OVERLAP_DAYS = 7
# UK banks allow four refreshes a day per account when you're not present.
# The schedule uses three, leaving one for a Sync you press yourself (which
# also counts unless a proxy passes on your public IP).
TRUELAYER_UNATTENDED_GAP = dt.timedelta(hours=8)

# One sync at a time. The schedule and the Sync button can otherwise overlap and
# both try to insert the same new transactions.
SYNC_LOCK = threading.Lock()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def rules_in_order(db: Session) -> list[CategoryRule]:
    """Your rules in the order they're checked, most specific first: see
    categorise.rule_order."""
    return sorted(db.scalars(select(CategoryRule)), key=rule_order)


def _rules(db: Session) -> list[CompiledRule]:
    """Your rules, in order and ready to check many transactions against."""
    return compile_rules(db.scalars(select(CategoryRule)))


def bank_key(txn: Transaction) -> str:
    """The merchant key the bank's name gives, whatever it's called now."""
    if txn.merchant_source and txn.bank_merchant_key is not None:
        return txn.bank_merchant_key
    return txn.merchant_key or (txn.merchant or txn.description or "").strip().lower()


def rule_outcome(txn: Transaction, rules: list[CompiledRule]) -> RuleOutcome:
    return evaluate_rules(
        rules,
        bank_key=bank_key(txn),
        description=txn.description,
        amount_minor=txn.amount_minor,
        account_id=txn.account_id,
        payment_type=txn.payment_type,
        own_key=txn.merchant_key if txn.merchant_source == "manual" else None,
        can_rename=txn.merchant_source != "manual",
    )


def apply_rule_actions(txn: Transaction, outcome: RuleOutcome) -> None:
    """Rename and exclude as the rules say. Your own name for a merchant, and
    your own say on whether a payment counts, are never changed."""
    if txn.merchant_source != "manual":
        if outcome.rename:
            if txn.merchant_source != "rule":  # first rename: keep the bank's name
                txn.bank_merchant, txn.bank_merchant_key = txn.merchant, txn.merchant_key
            txn.merchant, txn.merchant_key = outcome.rename, rule_key(outcome.rename)
            txn.merchant_source = "rule"
        elif txn.merchant_source == "rule":  # the rule's gone: back to the bank's name
            txn.merchant = txn.bank_merchant or txn.merchant
            txn.merchant_key = txn.bank_merchant_key if txn.bank_merchant_key is not None else txn.merchant_key
            txn.bank_merchant = txn.bank_merchant_key = txn.merchant_source = None

    if txn.excluded_source != "manual":
        if outcome.exclude:
            txn.excluded, txn.excluded_source = True, "rule"
        elif txn.excluded_source == "rule":
            txn.excluded, txn.excluded_source = False, None


def upsert_account(db: Session, provider: str, raw: RawAccount, connection: Connection | None) -> Account:
    account = db.scalar(select(Account).where(Account.provider == provider, Account.external_id == raw.external_id))
    if account is None:
        account = Account(provider=provider, external_id=raw.external_id)
        db.add(account)

    account.name = raw.name
    account.kind = raw.kind
    account.currency = raw.currency
    account.balance_minor = raw.balance_minor
    account.available_minor = raw.available_minor
    account.institution_name = raw.institution_name or (connection.institution_name if connection else "")
    account.balance_updated_at = _now()
    if connection is not None:
        account.connection_id = connection.id
    # Preserve user-set flags (e.g. flip_sign) while refreshing provider fields.
    meta = dict(account.provider_meta or {})
    meta.update(raw.meta)
    account.provider_meta = meta
    # Back in use when the bank sends it, unless you've taken it out of the totals.
    account.is_active = not meta.get("hidden")
    db.flush()
    return account


def _since_for(db: Session, account: Account) -> dt.date:
    """Where the next read starts: only what's new. A few days are read again
    because banks settle pending payments late, and so is any payment still
    pending, so it can settle or drop off. That's still one request."""
    latest = db.scalar(select(func.max(Transaction.booked_on)).where(Transaction.account_id == account.id))
    if not latest:
        return dt.date.today() - dt.timedelta(days=DEFAULT_LOOKBACK_DAYS)
    since = latest - dt.timedelta(days=OVERLAP_DAYS)
    oldest_pending = db.scalar(
        select(func.min(Transaction.booked_on)).where(
            Transaction.account_id == account.id, Transaction.status == "pending"
        )
    )
    return min(since, oldest_pending) if oldest_pending else since


def _fingerprint(booked_on: dt.date, amount_minor: int, description: str) -> tuple:
    return booked_on, amount_minor, " ".join((description or "").lower().split())


def upsert_transactions(db: Session, account: Account, raws: list[RawTransaction]) -> int:
    if not raws:
        return 0
    rules = _rules(db)
    existing = {t.external_id: t for t in db.scalars(select(Transaction).where(Transaction.account_id == account.id))}

    # Stored rows the bank now sends under a new ID (TrueLayer issues new IDs
    # when a bank without stable ones is connected again): matched one to one
    # by date, amount and description, so they're updated, not added again.
    sent = {raw.external_id for raw in raws}
    earliest = min(raw.booked_on for raw in raws)
    renamed: dict[tuple, list[Transaction]] = defaultdict(list)
    for txn in existing.values():
        if txn.external_id not in sent and txn.booked_on >= earliest:
            renamed[_fingerprint(txn.booked_on, txn.amount_minor, txn.description)].append(txn)

    added = 0
    for raw in raws:
        merchant_key = normalise_merchant(raw.merchant or raw.description)
        txn = existing.get(raw.external_id)
        if raw.status == "reversed":
            # The bank cancelled it (a declined or reversed card payment): the
            # pending copy stored earlier, matched by its own ID, must not count.
            if txn is not None and txn.status == "pending":
                db.delete(txn)
                existing.pop(raw.external_id, None)
            continue
        if txn is None and (same := renamed.get(_fingerprint(raw.booked_on, raw.amount_minor, raw.description))):
            txn = same.pop(0)
            existing.pop(txn.external_id, None)
            txn.external_id = raw.external_id
            existing[raw.external_id] = txn
        if txn is not None:
            # A pending row that settled, a statement re-imported with its
            # signs corrected, or an older row the bank now describes more
            # fully: refresh what the bank says, keep user edits.
            changed = (txn.amount_minor < 0) != (raw.amount_minor < 0)
            txn.amount_minor = raw.amount_minor
            txn.status = raw.status
            txn.booked_on = raw.booked_on
            if raw.payment_type and raw.payment_type != txn.payment_type:
                txn.payment_type = raw.payment_type
                changed = True
            if raw.provider_category and raw.provider_category != txn.provider_category:
                txn.provider_category = raw.provider_category
                changed = True
            if changed and txn.category_source != "manual" and not txn.is_transfer:
                txn.category, txn.category_source = _categorise_row(txn, rules)
            continue

        txn = Transaction(
            account_id=account.id,
            external_id=raw.external_id,
            booked_on=raw.booked_on,
            booked_at=raw.booked_at,
            amount_minor=raw.amount_minor,
            currency=raw.currency,
            description=raw.description,
            merchant=display_merchant(raw.merchant or raw.description),
            merchant_key=merchant_key,
            provider_category=raw.provider_category,
            mcc=raw.mcc,
            status=raw.status,
            payment_type=raw.payment_type,
            excluded=False,
        )
        outcome = rule_outcome(txn, rules)
        apply_rule_actions(txn, outcome)
        txn.category, txn.category_source = categorise(
            merchant=raw.merchant,
            description=raw.description,
            amount_minor=raw.amount_minor,
            mcc=raw.mcc,
            provider_category=raw.provider_category,
            payment_type=raw.payment_type,
            merchant_key=txn.merchant_key,
            outcome=outcome,
        )
        db.add(txn)
        existing[raw.external_id] = txn  # the same row twice in one batch is one row
        added += 1

    db.commit()
    return added


SYNC_LOG_KEEP_DAYS = 90


def prune_sync_logs(db: Session, keep_days: int = SYNC_LOG_KEEP_DAYS) -> int:
    """The sync history only needs recent months; it grows by a few rows a day."""
    cutoff = _now() - dt.timedelta(days=keep_days)
    old = list(db.scalars(select(SyncLog).where(SyncLog.started_at < cutoff)))
    for entry in old:
        db.delete(entry)
    db.commit()
    return len(old)


def _log(db: Session, provider: str, connection: Connection | None) -> SyncLog:
    entry = SyncLog(provider=provider, connection_id=connection.id if connection else None)
    db.add(entry)
    db.commit()
    return entry


def _finish(db: Session, entry: SyncLog, status: str, *, accounts: int = 0, added: int = 0, message: str | None = None):
    entry.status = status
    entry.finished_at = _now()
    entry.accounts_seen = accounts
    entry.transactions_added = added
    entry.message = message
    db.commit()


def _ensure_connection(db: Session, provider: str, name: str) -> Connection:
    conn = db.scalar(select(Connection).where(Connection.provider == provider))
    if conn is None:
        conn = Connection(provider=provider, institution_name=name, status="linked")
        db.add(conn)
        db.commit()
    return conn


# --- per-provider ------------------------------------------------------------
def sync_starling(db: Session) -> dict:
    connection = _ensure_connection(db, "starling", "Starling")
    entry = _log(db, "starling", connection)
    try:
        raw_accounts = starling.fetch_accounts()
        added = 0
        for raw in raw_accounts:
            account = upsert_account(db, "starling", raw, connection)
            txns = starling.fetch_transactions(raw, _since_for(db, account))
            added += upsert_transactions(db, account, txns)
        connection.status = "linked"
        connection.last_synced_at = _now()
        connection.last_error = None
        db.commit()
        _finish(db, entry, "ok", accounts=len(raw_accounts), added=added)
        return {"provider": "starling", "accounts": len(raw_accounts), "transactions_added": added}
    except starling.RateLimited as exc:
        # Not a fault: the token works. Retrying sooner would only prolong the
        # limit, so this waits for the next scheduled sync.
        connection.status = "linked"
        connection.last_error = str(exc)
        db.commit()
        _finish(db, entry, "ok", message=str(exc))
        return {"provider": "starling", "skipped": str(exc)}
    except ProviderError as exc:
        connection.status = "error"
        connection.last_error = str(exc)
        db.commit()
        _finish(db, entry, "error", message=str(exc))
        return {"provider": "starling", "error": str(exc)}


def sync_trading212(db: Session) -> dict:
    connection = _ensure_connection(db, "trading212", "Trading 212")
    entry = _log(db, "trading212", connection)
    try:
        raw_accounts = trading212.fetch_accounts()
        for raw in raw_accounts:
            upsert_account(db, "trading212", raw, connection)
        connection.status = "linked"
        connection.last_synced_at = _now()
        connection.last_error = None
        db.commit()
        _finish(db, entry, "ok", accounts=len(raw_accounts))
        return {"provider": "trading212", "accounts": len(raw_accounts)}
    except ProviderError as exc:
        connection.status = "error"
        connection.last_error = str(exc)
        db.commit()
        _finish(db, entry, "error", message=str(exc))
        return {"provider": "trading212", "error": str(exc)}


def truelayer_connections(db: Session) -> list[Connection]:
    return list(
        db.scalars(select(Connection).where(Connection.provider == "truelayer").order_by(Connection.created_at))
    )


def _attempted_within(connection: Connection, gap: dt.timedelta) -> bool:
    last = (connection.meta or {}).get("last_attempt_at")
    if not last:
        return False
    try:
        when = dt.datetime.fromisoformat(last)
    except ValueError:
        return False
    return _now() - when < gap


def sync_truelayer(
    db: Session,
    connection: Connection,
    *,
    psu_ip: str | None = None,
    tokens: truelayer.Tokens | None = None,
) -> dict:
    """One bank connected through TrueLayer. `tokens` are passed straight after
    connecting, so the first read happens while the bank still releases full
    history."""
    name = connection.institution_name
    base = {"provider": "truelayer", "connection_id": connection.id, "institution": name}
    entry = _log(db, "truelayer", connection)
    meta = dict(connection.meta or {})
    meta["last_attempt_at"] = _now().isoformat()
    connection.meta = meta
    db.commit()
    try:
        if tokens is None:
            if not connection.secret:
                raise truelayer.ConsentExpired("No access is stored for this bank. Connect it again.")
            tokens = truelayer.refresh(connection.secret)
        if tokens.refresh_token and tokens.refresh_token != connection.secret:
            # Keep a rotated token straight away, even if the rest of the sync fails.
            connection.secret = tokens.refresh_token
            db.commit()

        scopes = meta.get("scopes") or list(truelayer.WANTED_SCOPES)
        raw_accounts = truelayer.fetch_accounts(tokens.access_token, scopes, psu_ip, bank_name=name)
        added = 0
        for raw in raw_accounts:
            account = upsert_account(db, "truelayer", raw, connection)
            txns = truelayer.fetch_transactions(tokens.access_token, raw, _since_for(db, account), psu_ip)
            added += upsert_transactions(db, account, txns)
        connection.status = "linked"
        connection.last_synced_at = _now()
        connection.last_error = None
        db.commit()
        _finish(db, entry, "ok", accounts=len(raw_accounts), added=added)
        return {**base, "accounts": len(raw_accounts), "transactions_added": added}
    except truelayer.RateLimited as exc:
        # Not a fault: the bank's daily allowance ran out. The next sync catches up.
        connection.last_error = str(exc)
        db.commit()
        _finish(db, entry, "ok", message=str(exc))
        return {**base, "skipped": str(exc)}
    except truelayer.ConsentExpired as exc:
        connection.status = "expired"
        connection.last_error = str(exc)
        db.commit()
        _finish(db, entry, "error", message=str(exc))
        return {**base, "error": f"{name}: {exc}"}
    except ProviderError as exc:
        connection.status = "error"
        connection.last_error = str(exc)
        db.commit()
        _finish(db, entry, "error", message=str(exc))
        return {**base, "error": f"{name}: {exc}"}


def connect_truelayer(db: Session, code: str, *, psu_ip: str | None = None) -> dict:
    """Finish connecting a bank: swap the pasted code for tokens and read it.

    Connecting a bank that's already here (every 90 days) updates that
    connection, and its accounts are matched by account number, so nothing is
    duplicated."""
    tokens = truelayer.exchange_code(truelayer.parse_code(code))
    info = truelayer.connection_info(tokens.access_token, psu_ip)
    connection = next(
        (c for c in truelayer_connections(db) if (c.meta or {}).get("provider_id") == info.provider_id),
        None,
    )
    if connection is None:
        connection = Connection(provider="truelayer", institution_name=info.provider_name, status="pending")
        db.add(connection)
    connection.institution_name = info.provider_name
    connection.secret = tokens.refresh_token
    connection.expires_at = info.consent_expires_at
    connection.meta = {
        **(connection.meta or {}),
        "provider_id": info.provider_id,
        "scopes": info.scopes,
        "credentials_id": info.credentials_id,
    }
    db.commit()

    with SYNC_LOCK:
        result = sync_truelayer(db, connection, psu_ip=psu_ip, tokens=tokens)
        detect_transfers(db)
        recategorise_all(db)
        from ..analytics.networth import snapshot_networth  # local import avoids a cycle

        snapshot_networth(db)
    return result


def disconnect_truelayer(connection: Connection) -> None:
    """Ask TrueLayer to drop its access to the bank, then forget the token."""
    if connection.secret and settings.truelayer_configured:
        try:
            tokens = truelayer.refresh(connection.secret)
        except ProviderError:
            tokens = None  # already expired or revoked: nothing to drop
        if tokens is not None:
            truelayer.delete_credential(tokens.access_token)
    connection.secret = None


def sync_all(db: Session, *, unattended: bool = False, psu_ip: str | None = None) -> dict:
    """`unattended` is the schedule: TrueLayer banks are then read at most
    every TRUELAYER_UNATTENDED_GAP, to stay inside the banks' daily limit."""
    with SYNC_LOCK:
        results = []
        if settings.starling_configured:
            results.append(sync_starling(db))
        if settings.trading212_configured:
            results.append(sync_trading212(db))
        if settings.truelayer_configured:
            for connection in truelayer_connections(db):
                # An expired bank waits for you to connect it again; reporting
                # it as a failure would make the schedule retry everything.
                if connection.status == "expired":
                    continue
                if unattended and _attempted_within(connection, TRUELAYER_UNATTENDED_GAP):
                    continue
                results.append(sync_truelayer(db, connection, psu_ip=psu_ip))

        pairs = detect_transfers(db)
        # Rules and categoriser improvements reach older rows too, not only new ones.
        recategorise_all(db)
        from ..analytics.networth import snapshot_networth  # local import avoids a cycle

        snapshot_networth(db)
    return {
        "synced_at": _now().isoformat(),
        "results": results,
        "transfer_pairs": pairs,
        "errors": [r for r in results if "error" in r],
    }


def _categorise_row(txn: Transaction, rules: list[CompiledRule], outcome: RuleOutcome | None = None) -> tuple[str, str]:
    return categorise(
        merchant=txn.merchant,
        description=txn.description,
        amount_minor=txn.amount_minor,
        mcc=txn.mcc,
        provider_category=txn.provider_category,
        payment_type=txn.payment_type,
        merchant_key=txn.merchant_key,
        not_a_transfer=txn.transfer_group == NOT_A_TRANSFER,
        outcome=outcome if outcome is not None else rule_outcome(txn, rules),
    )


def categorise_one(db: Session, txn: Transaction) -> str:
    """The category a transaction would get by itself, under your rules."""
    return _categorise_row(txn, _rules(db))[0]


def recategorise_all(db: Session, *, include_manual: bool = False) -> int:
    """Re-run your rules and categorisation, e.g. after adding a rule. Money
    in then gets a second look with the whole history in view (salary,
    tutoring, you), and money out nothing could place is filed from what
    you've categorised before, where that's clear.

    Returns how many transactions now look different: a new category, a new
    name, or counted where they weren't (or the other way round)."""
    rules = _rules(db)
    rows = list(db.scalars(select(Transaction)))
    proposals = {}
    renamed_or_excluded: set[int] = set()
    for txn in rows:
        if txn.is_transfer:
            continue
        before = (txn.merchant, txn.excluded)
        outcome = rule_outcome(txn, rules)
        apply_rule_actions(txn, outcome)
        if (txn.merchant, txn.excluded) != before:
            renamed_or_excluded.add(txn.id)
        if include_manual or txn.category_source != "manual":
            proposals[txn.id] = _categorise_row(txn, rules, outcome)
    income.refine(rows, proposals, {a.id: a for a in db.scalars(select(Account))})
    learn.fill(rows, proposals)

    changed = set(renamed_or_excluded)
    for txn in rows:
        if txn.id not in proposals:
            continue
        category, source = proposals[txn.id]
        if (category, source) != (txn.category, txn.category_source):
            if category != txn.category:
                changed.add(txn.id)
            txn.category, txn.category_source = category, source
    db.commit()
    return len(changed)
