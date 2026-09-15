"""Reconcile landed-cost monetary destinations and their exact GL entries."""
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.inventory_cost_entry import InventoryCostEntry
from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.models.purchase_landed_cost_allocation_event import PurchaseLandedCostAllocationEvent
from app.models.purchase_landed_cost_capitalization import PurchaseLandedCostCapitalization
from app.models.purchase_landed_cost_event import PurchaseLandedCostEvent
from app.models.purchase_landed_cost_valuation_event import PurchaseLandedCostValuationEvent
from app.services.accounting_account_role_resolver import resolve_company_account_roles
from app.services.accounting_account_roles import AccountingAccountRole
from app.services.accounting_posting import post_journal_entry
from app.services.purchase_landed_cost_capitalization_service import _active_cost_condition
from app.services.purchase_landed_cost_flow_loader import load_landed_cost_flow
from app.services.purchase_landed_cost_flow_service import allocate_landed_cost_flow
from app.services.purchase_landed_cost_persistence_service import PurchaseLandedCostPersistenceError


async def _destination_account(db, *, company_id, destination, inventory_account_id):
    if destination.kind == "on_hand":
        return inventory_account_id
    cost = await db.get(InventoryCostEntry, destination.inventory_cost_entry_id)
    journal = (await db.execute(select(JournalEntry).options(selectinload(JournalEntry.lines)).where(
        JournalEntry.company_id == company_id, JournalEntry.document_id == cost.document_id,
        JournalEntry.reversal_of_id.is_(None), JournalEntry.status == "posted",
    ))).scalar_one_or_none()
    if journal is None:
        raise PurchaseLandedCostPersistenceError("Issued cost has no original posted accounting source")
    debit_accounts = {r.account_id for r in journal.lines if r.debit > 0}
    if len(debit_accounts) != 1:
        raise PurchaseLandedCostPersistenceError("Issued cost has an ambiguous historical debit account")
    debit_account_id = debit_accounts.pop()
    from app.models.account import Account
    account = await db.get(Account, debit_account_id)
    if account.account_type != "expense":
        raise PurchaseLandedCostPersistenceError("Non-expense issue requires explicit transfer or purchase-return provenance")
    return debit_account_id


async def _post_destination(db, *, allocation, source, destination, amount, debit_account_id,
                            credit_account_id, recognition_date, created_by):
    event = PurchaseLandedCostValuationEvent(
        company_id=source.company_id, allocation_event_id=allocation.id,
        destination_key=destination.key, destination_kind=destination.kind,
        product_id=destination.product_id, warehouse_id=destination.warehouse_id,
        stock_lot_id=destination.stock_lot_id,
        inventory_cost_entry_id=destination.inventory_cost_entry_id,
        amount=amount, recognition_date=recognition_date,
        debit_account_id=debit_account_id, credit_account_id=credit_account_id,
        created_by=created_by,
    )
    db.add(event)
    await db.flush()
    journal = JournalEntry(
        company_id=source.company_id, entry_date=recognition_date,
        description=f"Landed cost {source.id}; valuation event {event.id}",
        status="draft", created_by=created_by,
        lines=[
            JournalEntryLine(line_no=1, account_id=debit_account_id, debit=amount, credit=0),
            JournalEntryLine(line_no=2, account_id=credit_account_id, debit=0, credit=amount),
        ],
    )
    db.add(journal)
    await db.flush()
    await post_journal_entry(db, source.company_id, journal.id)
    event.journal_entry_id = journal.id
    await db.flush()
    return event


async def _reverse_destination(db, event, *, adjustment_date, created_by):
    if adjustment_date < event.recognition_date:
        raise PurchaseLandedCostPersistenceError("Adjustment cannot precede existing landed-cost valuation")
    from app.services.accounting_reversal import reverse_journal_entry
    journal = await reverse_journal_entry(
        db, company_id=event.company_id, journal_entry_id=event.journal_entry_id,
        reversal_date=adjustment_date, reversed_by=created_by,
        landed_cost_valuation_event_id=event.id,
    )
    reversal = PurchaseLandedCostValuationEvent(
        company_id=event.company_id, allocation_event_id=event.allocation_event_id,
        destination_key=event.destination_key, destination_kind=event.destination_kind,
        product_id=event.product_id, warehouse_id=event.warehouse_id,
        stock_lot_id=event.stock_lot_id, inventory_cost_entry_id=event.inventory_cost_entry_id,
        amount=event.amount, recognition_date=adjustment_date,
        debit_account_id=event.debit_account_id, credit_account_id=event.credit_account_id,
        created_by=created_by, reversal_of_id=event.id, journal_entry_id=journal.id,
    )
    db.add(reversal)
    await db.flush()
    return reversal


async def reconcile_landed_cost_valuation(
    db: AsyncSession, *, company_id: int, adjustment_date: date, created_by: int,
) -> tuple[PurchaseLandedCostValuationEvent, ...]:
    """Call after the full physical operation, in its transaction.

    Exact replay is a no-op. Changed destinations reverse previous snapshots
    and append replacements. Sources and historical quantities remain intact.
    """
    await db.flush()
    caps = list((await db.execute(select(PurchaseLandedCostCapitalization).where(
        PurchaseLandedCostCapitalization.company_id == company_id,
    ).order_by(PurchaseLandedCostCapitalization.id))).scalars().all())
    if not caps:
        return ()
    sources = {s.id: s for s in (await db.execute(select(PurchaseLandedCostEvent).where(
        PurchaseLandedCostEvent.company_id == company_id,
        PurchaseLandedCostEvent.id.in_([c.landed_cost_event_id for c in caps]),
    ))).scalars().all()}
    active_ids = set((await db.execute(select(PurchaseLandedCostEvent.id).where(
        PurchaseLandedCostEvent.company_id == company_id,
        PurchaseLandedCostEvent.id.in_(sources), _active_cost_condition(),
    ))).scalars().all())
    allocations = list((await db.execute(select(PurchaseLandedCostAllocationEvent).where(
        PurchaseLandedCostAllocationEvent.company_id == company_id,
        PurchaseLandedCostAllocationEvent.landed_cost_event_id.in_(sources),
    ).order_by(PurchaseLandedCostAllocationEvent.id))).scalars().all())
    history = list((await db.execute(select(PurchaseLandedCostValuationEvent).where(
        PurchaseLandedCostValuationEvent.company_id == company_id,
    ).order_by(PurchaseLandedCostValuationEvent.id).with_for_update())).scalars().all())
    reversed_ids = {e.reversal_of_id for e in history if e.reversal_of_id is not None}
    active = {}
    for event in history:
        if event.reversal_of_id is None and event.id not in reversed_ids:
            key = (event.allocation_event_id, event.destination_key)
            if key in active or event.journal_entry_id is None:
                raise PurchaseLandedCostPersistenceError("Inconsistent active landed-cost valuation history")
            active[key] = event
    graph, roots = await load_landed_cost_flow(
        db, company_id=company_id,
        product_ids={a.product_id for a in allocations if a.landed_cost_event_id in active_ids},
    ) if active_ids else ({}, {})
    accounts = await resolve_company_account_roles(
        db, company_id=company_id, roles=(AccountingAccountRole.INVENTORY_GOODS,),
    )
    inventory_account_id = accounts[AccountingAccountRole.INVENTORY_GOODS].id
    cap_by_source = {c.landed_cost_event_id: c for c in caps}
    created = []
    for allocation in allocations:
        source = sources[allocation.landed_cost_event_id]
        cap = cap_by_source[source.id]
        expense = await db.get(JournalEntryLine, cap.source_journal_entry_line_id)
        desired = ()
        if source.id in active_ids:
            root = roots.get(allocation.warehouse_document_line_id)
            if root is None:
                raise PurchaseLandedCostPersistenceError("Landed-cost receipt has no active valuation provenance")
            desired = allocate_landed_cost_flow(amount=allocation.allocated_amount, root=root, graph=graph)
        # Non-refundable costs on supplier returns belong back to the source
        # expense. Reversing their previous capitalization achieves this;
        # there is no new supplier liability or artificial same-account entry.
        desired_by_key = {destination.key: (destination, amount) for destination, amount in desired
                          if destination.kind != "expensed"}
        old_rows = {key[1]: row for key, row in active.items() if key[0] == allocation.id}
        for key in sorted(set(old_rows) | set(desired_by_key)):
            old = old_rows.get(key)
            target = desired_by_key.get(key)
            if target:
                destination, amount = target
                debit_account_id = await _destination_account(
                    db, company_id=company_id, destination=destination, inventory_account_id=inventory_account_id,
                )
                if old and old.amount == amount and old.debit_account_id == debit_account_id:
                    continue
            if old:
                created.append(await _reverse_destination(
                    db, old, adjustment_date=adjustment_date, created_by=created_by,
                ))
            if target:
                recognition_date = max(source.cost_date, destination.event_date,
                                       adjustment_date if old or history else source.cost_date)
                created.append(await _post_destination(
                    db, allocation=allocation, source=source, destination=destination, amount=amount,
                    debit_account_id=debit_account_id, credit_account_id=expense.account_id,
                    recognition_date=recognition_date, created_by=created_by,
                ))
    return tuple(created)


async def landed_cost_issue_amounts(db, *, company_id, as_of_date=None):
    statement = select(PurchaseLandedCostValuationEvent).where(
        PurchaseLandedCostValuationEvent.company_id == company_id,
        PurchaseLandedCostValuationEvent.destination_kind == "issued",
    )
    if as_of_date is not None:
        statement = statement.where(PurchaseLandedCostValuationEvent.recognition_date <= as_of_date)
    totals = {}
    for event in (await db.execute(statement)).scalars().all():
        key = event.inventory_cost_entry_id
        totals[key] = totals.get(key, Decimal(0)) + event.amount * (-1 if event.reversal_of_id else 1)
    return totals
