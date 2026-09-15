"""Caller-owned capitalization of already posted UAH expenses."""
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.account import Account
from app.models.company import Company
from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.models.purchase_landed_cost_capitalization import PurchaseLandedCostCapitalization
from app.models.purchase_landed_cost_event import PurchaseLandedCostEvent
from app.services.accounting_period_service import ensure_period_open
from app.services.purchase_landed_cost_persistence_service import (
    PurchaseLandedCostPersistenceError, PurchaseLandedCostPersistenceResult,
    _allocations, _amount, _business_date, _positive_id,
    create_purchase_landed_cost, reverse_purchase_landed_cost,
)


def _active_cost_condition():
    reversal = aliased(PurchaseLandedCostEvent)
    return ~select(reversal.id).where(
        reversal.company_id == PurchaseLandedCostEvent.company_id,
        reversal.reversal_of_id == PurchaseLandedCostEvent.id,
    ).exists()


async def lock_landed_cost_company(db, company_id):
    company = (await db.execute(select(Company).where(
        Company.id == company_id, Company.is_active.is_(True),
    ).with_for_update())).scalar_one_or_none()
    if company is None:
        raise PurchaseLandedCostPersistenceError("Active company not found")
    return company


async def has_capitalized_expenses_for_journal(db, *, company_id, journal_entry_id):
    return (await db.execute(select(PurchaseLandedCostCapitalization.id)
        .join(PurchaseLandedCostEvent,
              PurchaseLandedCostEvent.id == PurchaseLandedCostCapitalization.landed_cost_event_id)
        .join(JournalEntryLine,
              JournalEntryLine.id == PurchaseLandedCostCapitalization.source_journal_entry_line_id)
        .where(PurchaseLandedCostCapitalization.company_id == company_id,
               JournalEntryLine.journal_entry_id == journal_entry_id,
               _active_cost_condition()).limit(1))).scalar_one_or_none() is not None


async def capitalize_purchase_landed_cost(
    db: AsyncSession, *, company_id: int, source_journal_entry_line_id: int,
    request_key: str, trade_document_id: int, warehouse_document_id: int,
    amount: Decimal, cost_date: date, created_by: int, reason_code: str | None = None,
) -> PurchaseLandedCostPersistenceResult:
    """Reclassify an existing expense, never create another supplier payable.

    request_key identifies one business request across retries, even after its
    later reversal. A changed payload with the same key is rejected.
    """
    for field, value in (("company_id", company_id), ("source_journal_entry_line_id", source_journal_entry_line_id)):
        _positive_id(value, field)
    amount = _amount(amount)
    _business_date(cost_date, "cost_date")
    if not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 100:
        raise PurchaseLandedCostPersistenceError("request_key must contain 1 to 100 characters")
    await lock_landed_cost_company(db, company_id)
    previous = (await db.execute(select(PurchaseLandedCostCapitalization).where(
        PurchaseLandedCostCapitalization.company_id == company_id,
        PurchaseLandedCostCapitalization.request_key == request_key,
    ))).scalar_one_or_none()
    if previous:
        event = await db.get(PurchaseLandedCostEvent, previous.landed_cost_event_id)
        if (previous.source_journal_entry_line_id != source_journal_entry_line_id
                or event.trade_document_id != trade_document_id
                or event.warehouse_document_id != warehouse_document_id
                or event.amount != amount or event.cost_date != cost_date
                or event.created_by != created_by or event.reason_code != reason_code):
            raise PurchaseLandedCostPersistenceError("request_key already belongs to a different request")
        return PurchaseLandedCostPersistenceResult(event, await _allocations(db, event), False)

    source = (await db.execute(select(JournalEntryLine).join(JournalEntry).where(
        JournalEntryLine.id == source_journal_entry_line_id,
        JournalEntry.company_id == company_id,
    ))).scalar_one_or_none()
    if source is None:
        raise PurchaseLandedCostPersistenceError("Expense source not found in this company")
    journal = (await db.execute(select(JournalEntry).where(
        JournalEntry.id == source.journal_entry_id, JournalEntry.company_id == company_id,
    ).with_for_update().execution_options(populate_existing=True))).scalar_one()
    source = (await db.execute(select(JournalEntryLine).where(
        JournalEntryLine.id == source_journal_entry_line_id,
    ).with_for_update().execution_options(populate_existing=True))).scalar_one()
    account = (await db.execute(select(Account).where(
        Account.company_id == company_id, Account.id == source.account_id,
    ))).scalar_one_or_none()
    if (journal.status != "posted" or journal.reversal_of_id is not None
            or journal.entry_date > cost_date or source.credit != 0
            or not source.debit.is_finite() or source.debit <= 0
            or account is None or account.account_type != "expense"):
        raise PurchaseLandedCostPersistenceError("Source must be an original posted expense debit dated on/before cost_date")
    from app.models.purchase_landed_cost_valuation_event import PurchaseLandedCostValuationEvent
    if await db.scalar(select(PurchaseLandedCostValuationEvent.id).where(
        PurchaseLandedCostValuationEvent.journal_entry_id == journal.id,
    )) is not None:
        raise PurchaseLandedCostPersistenceError("A landed-cost adjustment cannot be used as a new expense source")
    used = await db.scalar(select(func.coalesce(func.sum(PurchaseLandedCostEvent.amount), 0))
        .join(PurchaseLandedCostCapitalization,
              PurchaseLandedCostCapitalization.landed_cost_event_id == PurchaseLandedCostEvent.id)
        .where(PurchaseLandedCostCapitalization.company_id == company_id,
               PurchaseLandedCostCapitalization.source_journal_entry_line_id == source.id,
               _active_cost_condition()))
    if amount > source.debit - used:
        raise PurchaseLandedCostPersistenceError("Amount exceeds unallocated expense debit")

    await ensure_period_open(company_id, cost_date, db)
    result = await create_purchase_landed_cost(
        db, company_id=company_id, trade_document_id=trade_document_id,
        warehouse_document_id=warehouse_document_id, amount=amount,
        currency_code="UAH", cost_date=cost_date, created_by=created_by, reason_code=reason_code,
    )
    db.add(PurchaseLandedCostCapitalization(
        company_id=company_id, landed_cost_event_id=result.event.id,
        source_journal_entry_line_id=source.id, request_key=request_key,
    ))
    await db.flush()
    from app.services.purchase_landed_cost_valuation_service import reconcile_landed_cost_valuation
    await reconcile_landed_cost_valuation(db, company_id=company_id, adjustment_date=cost_date, created_by=created_by)
    return result


async def reverse_capitalized_purchase_landed_cost(
    db: AsyncSession, *, company_id: int, landed_cost_event_id: int,
    reversal_date: date, reversed_by: int, reason_code: str | None = None,
) -> PurchaseLandedCostPersistenceResult:
    await lock_landed_cost_company(db, company_id)
    _business_date(reversal_date, "reversal_date")
    await ensure_period_open(company_id, reversal_date, db)
    result = await reverse_purchase_landed_cost(
        db, company_id=company_id, landed_cost_event_id=landed_cost_event_id,
        reversal_date=reversal_date, reversed_by=reversed_by, reason_code=reason_code,
    )
    from app.services.purchase_landed_cost_valuation_service import reconcile_landed_cost_valuation
    await reconcile_landed_cost_valuation(db, company_id=company_id, adjustment_date=reversal_date, created_by=reversed_by)
    return result
