"""AR: economic recognition/returns/clearing versus GL, with commercial bridge."""
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field
from app.services.unattributed_gl_control_service import unattributed_journals
from app.services.document_gl_control_service import reconcile_documents_gl
from sqlalchemy import case, func, select

from app.models.sales_recognition_event import SalesRecognitionEvent
from app.models.sales_return_recognition_event import SalesReturnRecognitionEvent
from app.models.customer_advance_clearing_event import CustomerAdvanceClearingEvent
from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.services.accounting_account_roles import AccountingAccountRole as Role
from app.services.accounting_account_role_resolver import resolve_company_account_roles
from app.services.event_gl_control_service import EventControlSpec, EventGlControl, reconcile_event_gl
from app.services.sales_ar_aging_projection_service import load_sales_ar_aging_projection

ZERO = Decimal("0")
SPECS = (
    EventControlSpec(SalesRecognitionEvent, "sales_recognition_event_id", "recognition_date",
        "recognized_gross_amount", Role.CUSTOMER_RECEIVABLES, Role.GOODS_REVENUE),
    EventControlSpec(SalesReturnRecognitionEvent, "sales_return_recognition_event_id", "recognition_date",
        "returned_gross_amount", Role.SALES_DEDUCTIONS, Role.CUSTOMER_RECEIVABLES),
    EventControlSpec(CustomerAdvanceClearingEvent, "customer_advance_clearing_event_id", "clearing_date",
        "cleared_amount", Role.CUSTOMER_ADVANCES, Role.CUSTOMER_RECEIVABLES),
)


class ArCommercialBridge(BaseModel):
    commercial_principal: Decimal
    commercial_settled: Decimal
    commercial_open: Decimal
    recognized_gross: Decimal
    recognized_returns: Decimal
    economic_clearing: Decimal
    legacy_document_balance: Decimal = Decimal(0)
    opening_gl: Decimal
    economic_balance: Decimal
    posted_balance: Decimal
    gl_difference: Decimal
    principal_less_recognition: Decimal
    settlement_less_clearing: Decimal
    commercial_less_economic: Decimal
    explanation: str


class ArGlControl(BaseModel):
    company_id: int
    date_from: date
    date_to: date
    matched: bool
    sources: list[EventGlControl]
    bridge: ArCommercialBridge
    unattributed_journal_ids: list[int] = Field(default_factory=list)


async def reconcile_ar_gl(db, *, company_id, date_from, date_to):
    if company_id <= 0 or date_from > date_to or (date_to - date_from).days > 366:
        raise ValueError("Provide a valid company and a range of at most 367 days")
    roles = tuple(dict.fromkeys(role for spec in SPECS for role in (spec.debit_role, spec.credit_role)))
    accounts = await resolve_company_account_roles(db, company_id=company_id, roles=roles)
    ids = {role: account.id for role, account in accounts.items()}
    if len(set(ids.values())) != len(ids):
        raise ValueError("AR control requires distinct role accounts")
    account_id = ids[Role.CUSTOMER_RECEIVABLES]
    sources = []
    totals = []
    for spec in SPECS:
        sources.append(await reconcile_event_gl(db, spec=spec, company_id=company_id,
            date_from=date_from, date_to=date_to, account_ids=ids, focus_account_id=account_id))
        model = spec.model
        value = getattr(model, spec.amount_field)
        total = await db.scalar(select(func.coalesce(func.sum(case(
            (model.reversal_of_id.is_(None), value), else_=-value)), 0)).where(
                model.company_id == company_id, getattr(model, spec.date_field) <= date_to))
        totals.append(Decimal(total))
    recognized, returned, cleared = totals
    from app.services.opening_settlement_gl_control_service import reconcile_opening_settlements
    opening_settlements,opening_clearing=await reconcile_opening_settlements(db,company_id=company_id,
        date_from=date_from,date_to=date_to,account_id=account_id,advance_id=ids[Role.CUSTOMER_ADVANCES],payable=False)
    sources.extend(opening_settlements)
    cleared-=opening_clearing
    document_sources, document_balance = await reconcile_documents_gl(db, company_id=company_id,
        date_from=date_from,date_to=date_to,focus_account_id=account_id,require_mapping=False)
    sources.extend(document_sources)
    amount = JournalEntryLine.debit - JournalEntryLine.credit
    posted, opening = (await db.execute(select(
        func.coalesce(func.sum(amount), 0),
        func.coalesce(func.sum(case((JournalEntry.opening_balance_id.is_not(None), amount), else_=0)), 0)
    ).select_from(JournalEntry).join(JournalEntryLine).where(
        JournalEntry.company_id == company_id, JournalEntry.entry_date <= date_to,
        JournalEntry.status.in_(("posted", "reversed")), JournalEntryLine.account_id == account_id
    ))).one()
    projections = await load_sales_ar_aging_projection(db, company_id=company_id, as_of_date=date_to)
    if any(p.currency_code != "UAH" for p in projections):
        raise ValueError("AR control requires UAH; foreign currencies must not be summed without FX")
    principal = sum((p.projection.original_amount for p in projections), ZERO)
    settled = sum((p.projection.settled_amount for p in projections), ZERO)
    commercial = sum((p.projection.open_amount for p in projections), ZERO)
    from app.services.opening_detail_gl_control_service import reconcile_opening_detail_gl
    detail_sources,detail_correction=await reconcile_opening_detail_gl(db,company_id=company_id,
        date_to=date_to,account_id=account_id,family='ar')
    sources.extend(detail_sources)
    economic = opening + detail_correction + document_balance + recognized - returned - cleared
    if not all(Decimal(v).is_finite() for v in (*totals, opening, posted, principal, settled, commercial)):
        raise ValueError("AR control encountered nonfinite amounts")
    bridge = ArCommercialBridge(
        commercial_principal=principal, commercial_settled=settled, commercial_open=commercial,
        recognized_gross=recognized, recognized_returns=returned, economic_clearing=cleared,
        opening_gl=opening, legacy_document_balance=document_balance, economic_balance=economic, posted_balance=posted,
        gl_difference=posted-economic, principal_less_recognition=principal-recognized,
        settlement_less_clearing=settled-cleared, commercial_less_economic=commercial-economic,
        explanation=("Commercial invoices and allocations differ from recognized sales, returns and clearing. "
                     "The displayed components explain the arithmetic difference; they do not certify "
                     "that every difference is valid timing. Opening GL requires separate subledger detail."))
    unknown = await unattributed_journals(db, company_id=company_id, account_id=account_id,
        date_from=date_from,date_to=date_to,specs=SPECS,documents=True,opening_settlements=True)
    return ArGlControl(company_id=company_id, date_from=date_from, date_to=date_to,
        matched=all(source.matched for source in sources) and posted == economic and not unknown,
        unattributed_journal_ids=unknown,
        sources=sources, bridge=bridge)
