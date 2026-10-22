"""Protected severance in final settlement (KZpP 129).

Disbursements pay protected severance first. Advances offset ordinary wages
only. Partial payments consume the protected balance in creation order; a
later payment may not predate an earlier reservation. Caller holds the company
payroll lock. No caller-supplied protection can reduce the derived minimum.
"""
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import aliased
from app.models.payroll_disbursement import PayrollDisbursement as Payment
from app.models.journal_entry import JournalEntry
from app.models.payroll_statutory import PayrollStatutoryResult, PayrollStatutoryResultLine
from app.services.payroll_severance_financial_guard import require_severance_financial_classification


async def severance_net(db, *, company_id, payroll_calculation_id):
    snapshot = await require_severance_financial_classification(
        db, company_id=company_id, payroll_calculation_id=payroll_calculation_id,
        error_type=ValueError, consumer='payment')
    if snapshot is None:
        return Decimal('0.00')
    result = await db.scalar(select(PayrollStatutoryResult).where(
        PayrollStatutoryResult.company_id == company_id,
        PayrollStatutoryResult.payroll_calculation_id == payroll_calculation_id))
    if result is None:
        raise ValueError('Severance payment requires statutory results')
    lines = list((await db.scalars(select(PayrollStatutoryResultLine).where(
        PayrollStatutoryResultLine.company_id == company_id,
        PayrollStatutoryResultLine.payroll_statutory_result_id == result.id))).all())
    components = {line.component: line for line in lines}
    tax = Decimal('0.00')
    for component, rate in [('personal_income_tax', Decimal('.18')), ('military_levy', Decimal('.05'))]:
        line = components.get(component)
        if line is None or line.rate != rate or line.base_amount < snapshot.severance_gross:
            raise ValueError('Severance payment tax evidence is inconsistent')
        # Attribute the rounding remainder to severance so the two income
        # categories reconcile to the actual immutable tax result, including
        # employee-wide cumulative rounding across concurrent contracts.
        ordinary_tax = ((line.base_amount - snapshot.severance_gross) * rate).quantize(
            Decimal('.01'), rounding=ROUND_HALF_UP)
        tax += line.amount - ordinary_tax
    net = snapshot.severance_gross - tax
    if not 0 <= net <= result.net_amount:
        raise ValueError('Severance protected net does not reconcile')
    return net


async def payment_protected_minimum(db, *, company_id, payment):
    if payment.payroll_calculation_id is None:
        return Decimal('0.00')
    protected = await severance_net(db, company_id=company_id,
        payroll_calculation_id=payment.payroll_calculation_id)
    if not protected:
        return protected
    reversal = aliased(JournalEntry)
    rows = (await db.execute(select(Payment, JournalEntry.status, reversal.entry_date)
        .outerjoin(JournalEntry, and_(JournalEntry.company_id == Payment.company_id,
            JournalEntry.payroll_disbursement_id == Payment.id, JournalEntry.reversal_of_id.is_(None)))
        .outerjoin(reversal, and_(reversal.company_id == JournalEntry.company_id,
            reversal.reversal_of_id == JournalEntry.id, reversal.status == 'posted'))
        .where(Payment.company_id == company_id,
            Payment.payroll_calculation_id == payment.payroll_calculation_id,
            Payment.id < payment.id,
            or_(Payment.cancelled_at.is_(None), JournalEntry.status.in_(('posted','reversed')))))).all()
    consumed = Decimal('0.00')
    for prior, status, reversed_on in rows:
        if status == 'reversed' and reversed_on is not None and reversed_on <= payment.payment_date:
            continue
        if status is None or status == 'draft':
            raise ValueError('Post or cancel the earlier severance payment reservation first')
        if prior.payment_date > payment.payment_date:
            raise ValueError('Severance payment cannot predate an earlier reserved payment')
        consumed += prior.amount + prior.withheld_amount
    return min(payment.amount + payment.withheld_amount, max(Decimal('0.00'), protected - consumed))


async def require_severance_payment_reversible(db, *, company_id, payment_id, error_type, reversal_date=None):
    payment = await db.scalar(select(Payment).where(Payment.company_id == company_id, Payment.id == payment_id))
    if payment is None or payment.payroll_calculation_id is None:
        return
    from app.models.payroll import PayrollCalculationLine
    exists = await db.scalar(select(PayrollCalculationLine.id).where(
        PayrollCalculationLine.company_id == company_id,
        PayrollCalculationLine.payroll_calculation_id == payment.payroll_calculation_id,
        PayrollCalculationLine.line_type == 'severance').limit(1))
    if exists is None:
        return
    reverse = aliased(JournalEntry)
    later = await db.scalar(select(Payment.id)
        .outerjoin(JournalEntry, and_(JournalEntry.company_id == Payment.company_id,
            JournalEntry.payroll_disbursement_id == Payment.id, JournalEntry.reversal_of_id.is_(None)))
        .outerjoin(reverse, and_(reverse.company_id == company_id,
            reverse.reversal_of_id == JournalEntry.id, reverse.status == 'posted'))
        .where(Payment.company_id == company_id,
            Payment.payroll_calculation_id == payment.payroll_calculation_id,
            Payment.id > payment.id,
            or_(and_(Payment.cancelled_at.is_(None),
                     or_(JournalEntry.id.is_(None), JournalEntry.status != 'reversed')),
                reverse.entry_date > reversal_date if reversal_date is not None else False)).limit(1))
    if later is not None:
        raise error_type('Reverse or cancel later severance payments first to preserve protected income allocation')
