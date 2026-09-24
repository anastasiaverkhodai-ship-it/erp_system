"""Materialize opening stock and debts without generating a second GL journal.

Caller owns the transaction. Company serialization matches warehouse operations;
all validation and materialization must commit together with the opening journal.
"""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import select, tuple_
from sqlalchemy.orm import selectinload
from app.models.opening_balance_detail import OpeningBalanceDetail
from app.models.opening_balance import OpeningBalance
from app.models.journal_entry import JournalEntry
from app.models.document import Document
from app.models.document_line import DocumentLine
from app.models.counterparty_open_item import CounterpartyOpenItem
from app.models.counterparty import Counterparty
from app.models.contract import Contract
from app.models.product import Product
from app.models.warehouse import Warehouse
from app.models.stock_ledger import StockLedger
from app.models.payment_settlement_allocation import PaymentSettlementAllocation
from app.services.opening_balance_service import OpeningBalanceError, get_opening_balance
from app.services.accounting_account_roles import AccountingAccountRole as R
from app.services.accounting_account_role_resolver import resolve_company_account_roles, AccountingAccountRoleResolutionError
from app.services.accounting_period_service import ensure_period_open
from app.services.idempotency_fingerprint_service import generate_request_fingerprint
from app.services.landed_cost_inventory_lifecycle import landed_cost_inventory_operation
from app.services.posting_context import create_posting_context
from app.services.warehouse_posting_handler import WarehousePostingHandler, WarehousePostingHandlerError

ZERO=Decimal(0)


@landed_cost_inventory_operation(result_date=lambda opening: opening.opening_date)
async def attach_opening_details(db, *, company_id, opening_balance_id, created_by, data):
    opening=await get_opening_balance(db, company_id, opening_balance_id)
    journal=await db.scalar(select(JournalEntry).options(selectinload(JournalEntry.lines)).where(
        JournalEntry.company_id==company_id, JournalEntry.id==opening.journal_entry_id
    ).with_for_update().execution_options(populate_existing=True))
    fingerprint=generate_request_fingerprint(data.model_dump(mode='json'))
    existing=await db.scalar(select(OpeningBalanceDetail).where(
        OpeningBalanceDetail.company_id==company_id, OpeningBalanceDetail.opening_balance_id==opening.id))
    if existing:
        if existing.request_fingerprint != fingerprint:
            raise OpeningBalanceError('Opening detail already exists with different data')
        return opening
    if created_by<=0 or journal.status!='posted':
        raise OpeningBalanceError('Detail requires a posted opening and a valid actor')
    await ensure_period_open(company_id=company_id, operation_date=opening.opening_date, db=db)
    try:
        accounts=await resolve_company_account_roles(db, company_id=company_id,
            roles=(R.INVENTORY_GOODS, R.CUSTOMER_RECEIVABLES, R.SUPPLIER_PAYABLES))
    except AccountingAccountRoleResolutionError as exc:
        raise OpeningBalanceError(str(exc)) from exc
    ids={role: account.id for role,account in accounts.items()}
    if len(set(ids.values()))!=3:
        raise OpeningBalanceError('Opening detail accounts must be distinct')
    totals={role:ZERO for role in ids}
    totals[R.INVENTORY_GOODS]=sum(((line.quantity*line.unit_cost).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        for line in data.stock), ZERO)
    for line in data.debts:
        if line.document_date>opening.opening_date:
            raise OpeningBalanceError('Original debt date cannot follow opening date')
        role=R.CUSTOMER_RECEIVABLES if line.item_type=='receivable' else R.SUPPLIER_PAYABLES
        totals[role]+=line.amount
        counterparty=await db.scalar(select(Counterparty).where(Counterparty.id==line.counterparty_id,
            Counterparty.company_id==company_id, Counterparty.is_active.is_(True)))
        if counterparty is None:
            raise OpeningBalanceError('Invalid or inactive opening counterparty')
        if line.contract_id is not None:
            # Closed legacy contracts can still have outstanding debt at cutover.
            contract=await db.scalar(select(Contract).where(Contract.id==line.contract_id,
                Contract.company_id==company_id, Contract.counterparty_id==line.counterparty_id))
            if contract is None or contract.currency_code!='UAH':
                raise OpeningBalanceError('Invalid opening contract or currency')
    for role, account_id in ids.items():
        debit=sum((line.debit for line in journal.lines if line.account_id==account_id), ZERO)
        credit=sum((line.credit for line in journal.lines if line.account_id==account_id), ZERO)
        expected_debit,expected_credit=(ZERO,totals[role]) if role==R.SUPPLIER_PAYABLES else (totals[role],ZERO)
        if (debit,credit)!=(expected_debit,expected_credit):
            raise OpeningBalanceError(f'Opening detail does not exactly match GL account {account_id}')
    if data.stock:
        for model,values in ((Product,{line.product_id for line in data.stock}),
                             (Warehouse,{line.warehouse_id for line in data.stock})):
            found=set((await db.scalars(select(model.id).where(model.company_id==company_id,
                model.id.in_(values),model.is_active.is_(True)))).all())
            if found!=values:
                raise OpeningBalanceError('Invalid or inactive opening product/warehouse')
        identities=[(line.product_id,line.warehouse_id) for line in data.stock]
        from sqlalchemy import or_
        if await db.scalar(select(StockLedger.id).join(Document,Document.id==StockLedger.document_id).where(
            StockLedger.company_id==company_id,
            tuple_(StockLedger.product_id,StockLedger.warehouse_id).in_(identities),
            or_(Document.status!='reversed',StockLedger.movement_date>opening.opening_date)).limit(1)):
            raise OpeningBalanceError('Opening stock conflicts with active or later stock history; reverse it first and use a cutover date after its reversal')
    package=OpeningBalanceDetail(company_id=company_id,opening_balance_id=opening.id,
        request_fingerprint=fingerprint,created_by=created_by)
    db.add(package)
    if data.stock:
        document=Document(company_id=company_id,number=f'OPENING-{opening.id}',document_type='receipt',
            document_date=opening.opening_date,status='posted',created_by=created_by,
            posted_at=datetime.now(timezone.utc).replace(tzinfo=None),lines=[DocumentLine(
                product_id=line.product_id,warehouse_id=line.warehouse_id,quantity=line.quantity,price=line.unit_cost
            ) for line in data.stock])
        db.add(document); await db.flush()
        package.stock_document_id=document.id
        await db.flush()
        context=create_posting_context(db,document,0,created_by)
        for line in document.lines:
            context.set_receipt_exact_valuation_amount(line.id,
                (line.quantity*line.price).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
        try:
            await WarehousePostingHandler().post(context)
        except WarehousePostingHandlerError as exc:
            raise OpeningBalanceError(str(exc)) from exc
    for line in data.debts:
        db.add(CounterpartyOpenItem(company_id=company_id,opening_balance_id=opening.id,
            opening_reference=line.reference,trade_document_id=None,counterparty_id=line.counterparty_id,
            contract_id=line.contract_id,item_type=line.item_type,status='open',document_date=line.document_date,
            due_date=line.due_date,currency_code='UAH',original_amount=line.amount))
    await db.flush()
    return opening


async def create_opening_package(db, *, company_id, created_by, data):
    from app.services.opening_balance_service import create_opening_balance, post_opening_balance
    opening=await create_opening_balance(db,company_id,created_by,data.opening)
    await post_opening_balance(db,company_id,opening.id)
    return await attach_opening_details(db,company_id=company_id,opening_balance_id=opening.id,
        created_by=created_by,data=data.details)


async def reverse_opening_detail(db, *, company_id, opening_balance_id, reversal_date, reversed_by):
    package=await db.scalar(select(OpeningBalanceDetail).where(
        OpeningBalanceDetail.company_id==company_id,OpeningBalanceDetail.opening_balance_id==opening_balance_id))
    if package is None:
        return
    items=(await db.scalars(select(CounterpartyOpenItem).where(CounterpartyOpenItem.company_id==company_id,
        CounterpartyOpenItem.opening_balance_id==opening_balance_id).with_for_update()
        .execution_options(populate_existing=True))).all()
    if items and await db.scalar(select(PaymentSettlementAllocation.id).where(
        PaymentSettlementAllocation.company_id==company_id,
        PaymentSettlementAllocation.open_item_id.in_([item.id for item in items]),
        PaymentSettlementAllocation.status=='active').limit(1)):
        raise OpeningBalanceError('Reverse opening debt settlements before reversing the opening')
    if items and await db.scalar(select(PaymentSettlementAllocation.id).where(
        PaymentSettlementAllocation.company_id==company_id,
        PaymentSettlementAllocation.open_item_id.in_([item.id for item in items]),
        PaymentSettlementAllocation.reversed_at.is_not(None),
        PaymentSettlementAllocation.reversed_at >= datetime.combine(reversal_date,datetime.max.time(),timezone.utc)).limit(1)):
        raise OpeningBalanceError('Opening reversal cannot predate settlement history')
    if package.stock_document_id is not None:
        from app.services.document_reversal import reverse_document, DocumentReversalError
        try:
            await reverse_document(db,company_id=company_id,document_id=package.stock_document_id,
                reversal_date=reversal_date,reversed_by=reversed_by)
        except DocumentReversalError as exc:
            raise OpeningBalanceError(str(exc)) from exc
    for item in items:
        item.status='cancelled'
    await db.flush()
