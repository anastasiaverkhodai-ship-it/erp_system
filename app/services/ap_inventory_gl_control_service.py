"""AP/inventory balance and source controls; commercial differences stay explicit."""
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from app.services.unattributed_gl_control_service import unattributed_journals
from sqlalchemy import case, func, select

from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.models.stock_ledger import StockLedger
from app.services.counterparty_open_item_types import CounterpartyOpenItemType
from app.services.sales_ar_aging_projection_service import load_sales_ar_aging_projection
from app.services.accounting_account_roles import AccountingAccountRole as R
from app.services.accounting_account_role_resolver import resolve_company_account_roles
from app.services.document_gl_control_service import reconcile_documents_gl
from app.services.purchase_gl_control_service import purchase_event_controls, SIMPLE_SPECS, DYNAMIC_SPECS
from app.services.event_gl_control_service import EventGlControl

ZERO=Decimal(0)


class EconomicGlControl(BaseModel):
    family: str
    company_id: int
    date_from: date
    date_to: date
    expected_amount: Decimal
    posted_amount: Decimal
    difference: Decimal
    opening_gl: Decimal
    document_balance: Decimal
    event_balance: Decimal
    matched: bool
    sources: list[EventGlControl]
    commercial: dict[str,Any]
    unattributed_journal_ids: list[int] = Field(default_factory=list)


async def reconcile_ap_inventory_gl(db, *, company_id, date_from, date_to, family):
    if family not in ('ap','inventory') or company_id<=0 or date_from>date_to or (date_to-date_from).days>366:
        raise ValueError('Provide a valid family, company and range of at most 367 days')
    roles=(R.SUPPLIER_PAYABLES,R.SUPPLIER_ADVANCES,R.VAT_INPUT,R.INVENTORY_GOODS,R.GOODS_COGS)
    accounts=await resolve_company_account_roles(db,company_id=company_id,roles=roles)
    ids={role:account.id for role,account in accounts.items()}
    if len(set(ids.values()))!=len(ids):
        raise ValueError('Economic control requires distinct role accounts')
    focus=R.SUPPLIER_PAYABLES if family=='ap' else R.INVENTORY_GOODS
    credit=family=='ap'
    documents,document_balance=await reconcile_documents_gl(db,company_id=company_id,date_from=date_from,
        date_to=date_to,focus_account_id=ids[focus],normal_credit=credit)
    events,event_balance=await purchase_event_controls(db,company_id=company_id,date_from=date_from,
        date_to=date_to,ids=ids,focus_role=focus)
    if family=='ap':
        from app.services.opening_settlement_gl_control_service import reconcile_opening_settlements
        opening_settlements,opening_clearing=await reconcile_opening_settlements(db,company_id=company_id,
            date_from=date_from,date_to=date_to,account_id=ids[focus],advance_id=ids[R.SUPPLIER_ADVANCES],payable=True)
        events.extend(opening_settlements)
        event_balance+=opening_clearing
    amount=JournalEntryLine.credit-JournalEntryLine.debit if credit else JournalEntryLine.debit-JournalEntryLine.credit
    posted,opening=(await db.execute(select(func.coalesce(func.sum(amount),0),
        func.coalesce(func.sum(case((JournalEntry.opening_balance_id.is_not(None),amount),else_=0)),0)
    ).select_from(JournalEntry).join(JournalEntryLine).where(JournalEntry.company_id==company_id,
        JournalEntry.status.in_(('posted','reversed')),JournalEntry.entry_date<=date_to,
        JournalEntryLine.account_id==ids[focus]))).one()
    from app.services.opening_detail_gl_control_service import reconcile_opening_detail_gl
    detail_sources,detail_correction=await reconcile_opening_detail_gl(db,company_id=company_id,
        date_to=date_to,account_id=ids[focus],family=family)
    events.extend(detail_sources)
    expected=opening+detail_correction+document_balance+event_balance
    if not all(Decimal(value).is_finite() for value in (posted,opening,expected)):
        raise ValueError('Nonfinite source or GL balance')
    if family=='ap':
        projections=await load_sales_ar_aging_projection(db,company_id=company_id,as_of_date=date_to,
            item_type=CounterpartyOpenItemType.PAYABLE)
        if any(p.currency_code!='UAH' for p in projections):
            raise ValueError('AP control supports UAH; foreign currencies require FX')
        principal=sum((p.projection.original_amount for p in projections),ZERO)
        settled=sum((p.projection.settled_amount for p in projections),ZERO)
        open_amount=sum((p.projection.open_amount for p in projections),ZERO)
        commercial=dict(principal=principal,settled=settled,open_amount=open_amount,
            commercial_less_economic=open_amount-expected,
            explanation='Commercial invoices minus allocations compared with receipts, VAT bridges, returns, value corrections and economic clearing. A difference is not automatically a valid timing difference.')
    else:
        rows=(await db.execute(select(StockLedger.product_id,StockLedger.warehouse_id,
            func.sum(StockLedger.quantity).label('quantity')).where(StockLedger.company_id==company_id,
                StockLedger.movement_date<=date_to).group_by(StockLedger.product_id,StockLedger.warehouse_id)
                .order_by(StockLedger.product_id,StockLedger.warehouse_id))).all()
        commercial=dict(quantities=[dict(product_id=p,warehouse_id=w,quantity=q) for p,w,q in rows],
            explanation='Physical quantities at date_to are shown separately by product and warehouse. Economic value includes posted warehouse-document plans, cost corrections, returns and landed costs; quantities are not monetary amounts.')
    sources=documents+events
    unknown=await unattributed_journals(db,company_id=company_id,account_id=ids[focus],date_from=date_from,date_to=date_to,
        specs=SIMPLE_SPECS+DYNAMIC_SPECS[:-1],documents=True,landed=True,opening_settlements=family=='ap')
    return EconomicGlControl(family=family,company_id=company_id,date_from=date_from,date_to=date_to,
        expected_amount=expected,posted_amount=posted,difference=posted-expected,opening_gl=opening,
        document_balance=document_balance,event_balance=event_balance,
        matched=all(source.matched for source in sources) and posted==expected and not unknown,sources=sources,
        unattributed_journal_ids=unknown,
        commercial=commercial)
