"""Explicit pre-invoice advances from immutable confirmed order terms.

All operations participate in the caller's transaction. Transfer is a technical
source correction on the original payment dates and requires those periods open.
INPUT rows record economic capacity only; legal credit remains evidence-gated.
"""
from contextvars import ContextVar
from datetime import date, datetime, timezone
from decimal import Decimal
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload, aliased
from app.models.order_vat_advance import OrderVatAdvance, OrderVatAdvanceLine, OrderVatAdvanceTransfer
from app.models.payment import Payment
from app.models.trade_document import TradeDocument
from app.models.counterparty import Counterparty
from app.models.counterparty_open_item import CounterpartyOpenItem
from app.models.tax_calculation import TaxCalculation
from app.models.tax_recognition_event import TaxRecognitionEvent
from app.models.payment_settlement_allocation import PaymentSettlementAllocation
from app.models.invoice_fulfillment_allocation import InvoiceFulfillmentAllocation
from app.models.tax_credit_evidence import TaxCreditEvidence
from app.services.invoice_tax_calculation_service import build_invoice_tax_calculation
from app.services.tax_recognition_orchestration_service import build_settlement_recognition_candidate, validate_recognition_rate_dates, build_economic_recognition_timeline
from app.services.tax_recognition_journal_service import generate_and_post_output_vat_recognition_journal_entry, reverse_output_vat_recognition_journal_entry
from app.services.accounting_period_service import ensure_period_open
from app.services.vat_first_event_context_service import validate_first_event_context

_transfer_correction_date = ContextVar('order_advance_transfer_correction_date', default=None)


class OrderVatAdvanceError(ValueError):
    pass


async def lock_party(db, company_id, counterparty_id):
    party = await db.scalar(select(Counterparty).where(Counterparty.company_id == company_id,
        Counterparty.id == counterparty_id).with_for_update().execution_options(populate_existing=True))
    if party is None:
        raise OrderVatAdvanceError('Counterparty not found in company')


async def assert_payment_unbound(db, *, company_id, payment_id):
    if _transfer_correction_date.get() is not None:
        return
    if await db.scalar(select(OrderVatAdvance.id).where(OrderVatAdvance.company_id == company_id,
        OrderVatAdvance.payment_id == payment_id, OrderVatAdvance.status != 'reversed').limit(1)):
        raise OrderVatAdvanceError('Payment belongs to an order VAT advance; use advance transfer or undo-transfer')


async def assert_order_unbound(db, *, company_id, order_id):
    if await db.scalar(select(OrderVatAdvance.id).where(OrderVatAdvance.company_id == company_id,
        OrderVatAdvance.order_id == order_id, OrderVatAdvance.status != 'reversed').limit(1)):
        raise OrderVatAdvanceError('Order has VAT advances; undo transfer and reverse advances before cancellation')


async def assert_invoice_has_no_pending_advances(db, *, invoice):
    if _transfer_correction_date.get() is not None:
        return
    # Party lock also serializes advance creation with first invoice recognition.
    await lock_party(db, invoice.company_id, invoice.counterparty_id)
    pending = await db.scalar(select(OrderVatAdvance.id).join(TradeDocument,
        TradeDocument.id == OrderVatAdvance.order_id).where(
        OrderVatAdvance.company_id == invoice.company_id, OrderVatAdvance.status == 'active',
        TradeDocument.counterparty_id == invoice.counterparty_id,
        TradeDocument.contract_id == invoice.contract_id,
        TradeDocument.direction == invoice.direction).limit(1))
    if pending:
        raise OrderVatAdvanceError('Pending order advance for this party/contract: transfer it to the matching invoice before recognition')


async def _order(db, company_id, order_id, *, unfulfilled=False):
    order = await db.scalar(select(TradeDocument).where(TradeDocument.company_id == company_id,
        TradeDocument.id == order_id).options(selectinload(TradeDocument.lines)).with_for_update()
        .execution_options(populate_existing=True))
    if order is None or order.kind != 'order' or order.status not in ('confirmed', 'partially_fulfilled', 'fulfilled'):
        raise OrderVatAdvanceError('Advance requires a confirmed, unfulfilled order')
    if unfulfilled and order.status != 'confirmed':
        raise OrderVatAdvanceError('New advance requires an unfulfilled order')
    if order.currency_code != 'UAH' or not order.lines:
        raise OrderVatAdvanceError('Advance requires UAH and at least one order line')
    return order


def _terms(line):
    return tuple(getattr(line, name) for name in ('line_number', 'product_id', 'warehouse_id', 'quantity',
        'unit_price', 'tax_rate_code', 'tax_recognition_method', 'tax_price_mode', 'tax_legal_basis', 'no_vat_reason'))


async def _post_advance(db, advance, actor):
    for line in (await db.scalars(select(OrderVatAdvanceLine).where(
        OrderVatAdvanceLine.company_id == advance.company_id, OrderVatAdvanceLine.advance_id == advance.id))).all():
        calc = await db.get(TaxCalculation, line.tax_calculation_id)
        if calc.direction != 'output' or line.base + line.tax == 0:
            continue
        event = TaxRecognitionEvent(company_id=advance.company_id, tax_calculation_id=calc.id,
            order_vat_advance_id=advance.id, recognition_date=advance.event_date,
            recognized_taxable_base=line.base, recognized_tax_amount=line.tax, currency_code='UAH', created_by=actor)
        db.add(event); await db.flush()
        await generate_and_post_output_vat_recognition_journal_entry(db, event=event, created_by=actor)


async def _reverse_advance_events(db, advance, actor):
    reversal = aliased(TaxRecognitionEvent)
    events = (await db.scalars(select(TaxRecognitionEvent).where(
        TaxRecognitionEvent.company_id == advance.company_id,
        TaxRecognitionEvent.order_vat_advance_id == advance.id, TaxRecognitionEvent.reversal_of_id.is_(None),
        ~select(reversal.id).where(reversal.reversal_of_id == TaxRecognitionEvent.id).exists()
    ).order_by(TaxRecognitionEvent.id))).all()
    for original in events:
        event = TaxRecognitionEvent(company_id=advance.company_id, tax_calculation_id=original.tax_calculation_id,
            order_vat_advance_id=advance.id, recognition_date=original.recognition_date,
            recognized_taxable_base=original.recognized_taxable_base, recognized_tax_amount=original.recognized_tax_amount,
            currency_code='UAH', created_by=actor, reversal_of_id=original.id)
        db.add(event); await db.flush()
        await reverse_output_vat_recognition_journal_entry(db, reversal_event=event, reversed_by=actor)


async def create_order_vat_advance(db, *, company_id, order_id, payment_id, created_by):
    from app.services.payment_settlement_service import get_active_payment_settled_amount
    identity = await db.scalar(select(TradeDocument).where(TradeDocument.company_id == company_id, TradeDocument.id == order_id))
    if identity is None or created_by <= 0:
        raise OrderVatAdvanceError('Invalid order or actor')
    await lock_party(db, company_id, identity.counterparty_id)
    payment = await db.scalar(select(Payment).where(Payment.company_id == company_id, Payment.id == payment_id)
        .with_for_update().execution_options(populate_existing=True))
    order = await _order(db, company_id, order_id, unfulfilled=True)
    existing = await db.scalar(select(OrderVatAdvance).where(OrderVatAdvance.company_id == company_id, OrderVatAdvance.payment_id == payment_id))
    if existing:
        if existing.order_id == order_id and existing.status == 'active':
            return existing
        raise OrderVatAdvanceError('Payment already has an advance history; cannot rebind it')
    if payment is None or payment.status != 'confirmed' or payment.currency_code != 'UAH':
        raise OrderVatAdvanceError('Advance requires a confirmed UAH payment')
    if (payment.counterparty_id, payment.contract_id, payment.direction) != (
        order.counterparty_id, order.contract_id, 'incoming' if order.direction == 'sale' else 'outgoing'):
        raise OrderVatAdvanceError('Payment party, contract or direction differs from order')
    if payment.payment_date < order.document_date:
        raise OrderVatAdvanceError('Order must exist on payment date')
    if await get_active_payment_settled_amount(db, company_id=company_id, payment_id=payment_id):
        raise OrderVatAdvanceError('Payment already has active invoice settlements')
    # Pre-invoice workflow is deliberately explicit. Existing invoices must use normal settlement.
    if await db.scalar(select(TradeDocument.id).where(TradeDocument.company_id == company_id,
        TradeDocument.counterparty_id == order.counterparty_id, TradeDocument.contract_id == order.contract_id,
        TradeDocument.direction == order.direction, TradeDocument.kind == 'invoice',
        TradeDocument.status == 'confirmed').limit(1)):
        raise OrderVatAdvanceError('Confirmed invoice already exists for this party/contract; use invoice settlement')
    await ensure_period_open(db=db, company_id=company_id, operation_date=payment.payment_date)
    calculations = []
    for line in sorted(order.lines, key=lambda item: item.line_number):
        calc = await db.scalar(select(TaxCalculation).where(TaxCalculation.company_id == company_id,
            TaxCalculation.trade_document_id == order.id, TaxCalculation.trade_document_line_id == line.id))
        if calc is None:
            calc = build_invoice_tax_calculation(document=order, line=line)
            if calc is None or calc.recognition_method not in ('first_event', 'cash_method'):
                raise OrderVatAdvanceError('Order advances currently require automatic VAT lines (including VAT0)')
            db.add(calc); await db.flush()
        calculations.append(calc)
    total = sum((c.taxable_base + c.tax_amount for c in calculations), Decimal(0))
    used = await db.scalar(select(func.coalesce(func.sum(OrderVatAdvance.amount), 0)).where(
        OrderVatAdvance.company_id == company_id, OrderVatAdvance.order_id == order_id, OrderVatAdvance.status != 'reversed'))
    if not payment.amount.is_finite() or payment.amount <= 0 or used + payment.amount > total:
        raise OrderVatAdvanceError('Advance exceeds remaining order gross amount')
    advance = OrderVatAdvance(company_id=company_id, order_id=order_id, payment_id=payment_id,
        amount=payment.amount, event_date=payment.payment_date, created_by=created_by)
    db.add(advance); await db.flush()
    for calc in calculations:
        candidate = build_settlement_recognition_candidate(calculation=calc, source_id=advance.id,
            event_date=advance.event_date, settlement_amount=advance.amount, invoice_total_amount=total)
        timeline = build_economic_recognition_timeline(candidates=[candidate], calculated_base=calc.taxable_base, calculated_tax=calc.tax_amount)
        validate_recognition_rate_dates(calculation=calc, timeline=timeline)
        await validate_first_event_context(db, calculation=calc, candidates=[candidate], invoice=order)
        prior = (await db.execute(select(func.coalesce(func.sum(OrderVatAdvanceLine.base), 0),
            func.coalesce(func.sum(OrderVatAdvanceLine.tax), 0)).join(OrderVatAdvance,
            OrderVatAdvance.id == OrderVatAdvanceLine.advance_id).where(OrderVatAdvance.company_id == company_id,
            OrderVatAdvance.status != 'reversed', OrderVatAdvanceLine.tax_calculation_id == calc.id))).one()
        base, tax = candidate.taxable_base_capacity, candidate.tax_amount_capacity
        if prior[0] + base > calc.taxable_base or prior[1] + tax > calc.tax_amount:
            raise OrderVatAdvanceError('Rounded partial advances exceed line capacity; consolidate payments')
        if base + tax:
            db.add(OrderVatAdvanceLine(company_id=company_id, advance_id=advance.id,
                tax_calculation_id=calc.id, base=base, tax=tax, gross=base + tax))
    await db.flush()
    await _post_advance(db, advance, created_by)
    return advance


async def reverse_order_vat_advance(db, *, company_id, advance_id, reversed_by):
    identity = await db.scalar(select(OrderVatAdvance).where(OrderVatAdvance.company_id == company_id, OrderVatAdvance.id == advance_id))
    if identity is None:
        raise OrderVatAdvanceError('Advance not found')
    await db.scalar(select(Payment).where(Payment.company_id == company_id, Payment.id == identity.payment_id).with_for_update())
    advance = await db.scalar(select(OrderVatAdvance).where(OrderVatAdvance.id == advance_id,
        OrderVatAdvance.company_id == company_id).with_for_update().execution_options(populate_existing=True))
    if advance.status == 'reversed':
        return advance
    if advance.status != 'active' or reversed_by <= 0:
        raise OrderVatAdvanceError('Undo invoice transfer before correcting the advance')
    await ensure_period_open(db=db, company_id=company_id, operation_date=advance.event_date)
    await _reverse_advance_events(db, advance, reversed_by)
    advance.status = 'reversed'; advance.closed_at = datetime.now(timezone.utc); advance.closed_by = reversed_by
    await db.flush()
    return advance


async def transfer_order_advances(db, *, company_id, order_id, invoice_id, created_by):
    from app.services.payment_settlement_service import create_payment_settlement_allocation
    identity = await db.scalar(select(TradeDocument).where(TradeDocument.company_id == company_id, TradeDocument.id == order_id))
    if identity is None or created_by <= 0:
        raise OrderVatAdvanceError('Order or actor not found')
    await lock_party(db, company_id, identity.counterparty_id)
    advances = list((await db.scalars(select(OrderVatAdvance).where(OrderVatAdvance.company_id == company_id,
        OrderVatAdvance.order_id == order_id, OrderVatAdvance.status != 'reversed').order_by(OrderVatAdvance.payment_id))).all())
    if not advances:
        raise OrderVatAdvanceError('No active order advances')
    if all(a.status == 'transferred' and a.invoice_id == invoice_id for a in advances):
        return advances
    if any(a.status != 'active' for a in advances):
        raise OrderVatAdvanceError('Order advances already transferred to another invoice')
    for advance in advances:
        await db.scalar(select(Payment).where(Payment.company_id == company_id, Payment.id == advance.payment_id).with_for_update())
        await db.refresh(advance, with_for_update=True)
        if advance.status != 'active':
            raise OrderVatAdvanceError('Advance state changed; retry')
    order = await _order(db, company_id, order_id)
    invoice = await db.scalar(select(TradeDocument).where(TradeDocument.company_id == company_id, TradeDocument.id == invoice_id)
        .options(selectinload(TradeDocument.lines)).with_for_update().execution_options(populate_existing=True))
    if invoice is None or invoice.kind != 'invoice' or invoice.status != 'confirmed':
        raise OrderVatAdvanceError('Confirmed invoice required')
    if any(getattr(order, field) != getattr(invoice, field) for field in ('counterparty_id', 'contract_id', 'direction', 'currency_code')):
        raise OrderVatAdvanceError('Invoice context differs from order')
    if sorted(map(_terms, order.lines)) != sorted(map(_terms, invoice.lines)):
        raise OrderVatAdvanceError('Transfer requires identical order/invoice lines, prices and VAT settings')
    if invoice.document_date < max(a.event_date for a in advances):
        raise OrderVatAdvanceError('Invoice must not predate its advances')
    await _assert_invoice_pristine(db, company_id, invoice_id)
    item = await db.scalar(select(CounterpartyOpenItem).where(CounterpartyOpenItem.company_id == company_id,
        CounterpartyOpenItem.trade_document_id == invoice_id).with_for_update())
    if item is None:
        raise OrderVatAdvanceError('Invoice open item not found')
    calculations = list((await db.scalars(select(TaxCalculation).where(TaxCalculation.company_id == company_id,
        TaxCalculation.trade_document_id == invoice_id))).all())
    order_calcs = list((await db.scalars(select(TaxCalculation).where(TaxCalculation.company_id == company_id,
        TaxCalculation.trade_document_id == order_id))).all())
    def signature(c):
        return (c.product_id, c.tax_rate_code, c.tax_rate, c.treatment, c.recognition_method, c.taxable_base, c.tax_amount)
    if sorted(map(signature, calculations)) != sorted(map(signature, order_calcs)):
        raise OrderVatAdvanceError('Invoice tax snapshot differs from advance order')
    for advance in advances:
        await ensure_period_open(db=db, company_id=company_id, operation_date=advance.event_date)
        await _reverse_advance_events(db, advance, created_by)
        advance.status = 'transferred'; advance.invoice_id = invoice_id
        advance.closed_at = datetime.now(timezone.utc); advance.closed_by = created_by
    await db.flush()
    for advance in sorted(advances, key=lambda a: (a.event_date, a.payment_id)):
        token = _transfer_correction_date.set(advance.event_date)
        try:
            allocation = await create_payment_settlement_allocation(db, company_id=company_id,
                payment_id=advance.payment_id, open_item_id=item.id, amount=advance.amount, created_by=created_by)
        finally:
            _transfer_correction_date.reset(token)
        db.add(OrderVatAdvanceTransfer(company_id=company_id, advance_id=advance.id, allocation_id=allocation.id, created_by=created_by))
    await db.flush()
    return advances


async def _assert_invoice_pristine(db, company_id, invoice_id, *, allow_allocations=()):
    if await db.scalar(select(InvoiceFulfillmentAllocation.id).where(InvoiceFulfillmentAllocation.company_id == company_id,
        InvoiceFulfillmentAllocation.invoice_id == invoice_id, InvoiceFulfillmentAllocation.status == 'active').limit(1)):
        raise OrderVatAdvanceError('Undo invoice fulfillment matching before advance transfer correction')
    if await db.scalar(select(PaymentSettlementAllocation.id).join(CounterpartyOpenItem,
        CounterpartyOpenItem.id == PaymentSettlementAllocation.open_item_id).where(
        PaymentSettlementAllocation.company_id == company_id, CounterpartyOpenItem.trade_document_id == invoice_id,
        PaymentSettlementAllocation.status == 'active', PaymentSettlementAllocation.id.not_in(allow_allocations)).limit(1)):
        raise OrderVatAdvanceError('Invoice has other active payment settlements')
    if await db.scalar(select(TaxCreditEvidence.id).join(TaxCalculation, TaxCalculation.id == TaxCreditEvidence.tax_calculation_id)
        .where(TaxCalculation.company_id == company_id, TaxCalculation.trade_document_id == invoice_id).limit(1)):
        raise OrderVatAdvanceError('Invoice has legal credit evidence; advance source correction requires separate reviewed adjustment')


async def undo_order_advance_transfer(db, *, company_id, order_id, reversed_by):
    from app.services.payment_settlement_service import reverse_payment_settlement_allocation
    identity = await db.scalar(select(TradeDocument).where(TradeDocument.company_id == company_id, TradeDocument.id == order_id))
    if identity is None or reversed_by <= 0:
        raise OrderVatAdvanceError('Order or actor not found')
    await lock_party(db, company_id, identity.counterparty_id)
    advances = list((await db.scalars(select(OrderVatAdvance).where(OrderVatAdvance.company_id == company_id,
        OrderVatAdvance.order_id == order_id, OrderVatAdvance.status == 'transferred').order_by(OrderVatAdvance.payment_id))).all())
    if not advances:
        raise OrderVatAdvanceError('No transferred advances')
    for advance in advances:
        await db.scalar(select(Payment).where(Payment.company_id == company_id, Payment.id == advance.payment_id).with_for_update())
        await db.refresh(advance, with_for_update=True)
        if advance.status != 'transferred':
            raise OrderVatAdvanceError('Advance state changed; retry')
    await _order(db, company_id, order_id)
    transfers = list((await db.scalars(select(OrderVatAdvanceTransfer).where(
        OrderVatAdvanceTransfer.company_id == company_id, OrderVatAdvanceTransfer.advance_id.in_([a.id for a in advances]),
        OrderVatAdvanceTransfer.reversed_at.is_(None)))).all())
    if len(transfers) != len(advances):
        raise OrderVatAdvanceError('Incomplete transfer history')
    invoice_id = advances[0].invoice_id
    await db.scalar(select(TradeDocument).where(TradeDocument.company_id == company_id, TradeDocument.id == invoice_id).with_for_update())
    await _assert_invoice_pristine(db, company_id, invoice_id, allow_allocations=[t.allocation_id for t in transfers])
    by_id = {a.id: a for a in advances}
    # Reverse newest sources first; no earlier source gets redistributed during correction.
    for transfer in sorted(transfers, key=lambda t: (by_id[t.advance_id].event_date, t.id), reverse=True):
        advance = by_id[transfer.advance_id]
        await ensure_period_open(db=db, company_id=company_id, operation_date=advance.event_date)
        token = _transfer_correction_date.set(advance.event_date)
        try:
            await reverse_payment_settlement_allocation(db, company_id=company_id,
                allocation_id=transfer.allocation_id, reversed_by=reversed_by)
        finally:
            _transfer_correction_date.reset(token)
        transfer.reversed_at = datetime.now(timezone.utc); transfer.reversed_by = reversed_by
    for advance in advances:
        advance.status = 'active'; advance.invoice_id = None; advance.closed_at = None; advance.closed_by = None
        await _post_advance(db, advance, reversed_by)
    await db.flush()
    return advances
