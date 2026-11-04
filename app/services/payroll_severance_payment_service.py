"""Protected severance in final settlement (KZpP 129).

Disbursements pay protected severance first. Advances offset ordinary wages
only. Partial payments consume the protected balance in creation order; a
later payment may not predate an earlier reservation. Caller holds the company
payroll lock. No caller-supplied protection can reduce the derived minimum.
"""
from decimal import Decimal
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import aliased
from app.models.payroll_disbursement import PayrollDisbursement as Payment
from app.models.journal_entry import JournalEntry
from app.services.payroll_severance_financial_guard import require_severance_financial_classification


async def severance_net(db, *, company_id, payroll_calculation_id):
    snapshot = await require_severance_financial_classification(
        db, company_id=company_id, payroll_calculation_id=payroll_calculation_id,
        error_type=ValueError, consumer='payment')
    if snapshot is None:
        return Decimal('0.00')
    from app.services.payroll_severance_calculated_financial import load_calculated_severance_financial
    from app.services.payroll_severance_trusted_classification import TRUSTED_POLICIES
    financial = await load_calculated_severance_financial(
        db, company_id=company_id, calculation_id=payroll_calculation_id,
        income_date=snapshot.income_date, policies=TRUSTED_POLICIES)
    return financial.net


async def payment_protected_minimum(db, *, company_id, payment):
    if payment.payroll_calculation_id is None:
        return Decimal('0.00')
    protected = await severance_net(db, company_id=company_id,
        payroll_calculation_id=payment.payroll_calculation_id)
    if not protected:
        return protected
    from app.services.payroll_sick_financial_sources import sick_settlement_chain
    settlement_ids = await sick_settlement_chain(db, company_id=company_id,
        calculation_id=payment.payroll_calculation_id)
    reversal = aliased(JournalEntry)
    rows = (await db.execute(select(Payment, JournalEntry.status, reversal.entry_date, JournalEntry.id)
        .outerjoin(JournalEntry, and_(JournalEntry.company_id == Payment.company_id,
            JournalEntry.payroll_disbursement_id == Payment.id, JournalEntry.reversal_of_id.is_(None)))
        .outerjoin(reversal, and_(reversal.company_id == JournalEntry.company_id,
            reversal.reversal_of_id == JournalEntry.id, reversal.status == 'posted'))
        .where(Payment.company_id == company_id,
            Payment.payroll_calculation_id.in_(settlement_ids),
            Payment.id < payment.id,
            or_(Payment.cancelled_at.is_(None), JournalEntry.status.in_(('posted','reversed'))))
        .order_by(Payment.id))).all()
    # Process real returns in the same cash chronology as payments. Only a
    # reviewed severance decrease restores protected income; ordinary sick or
    # wage refunds cannot create a new severance exemption.
    from app.models.payroll_sick_return import PayrollSickReturn as Return
    from app.models.payroll_sick_correction_application import PayrollSickPayrollRevision as Revision, PayrollSickCorrectionApplication as Application
    returns = (await db.execute(select(Return, JournalEntry.entry_date, Revision)
        .join(JournalEntry, and_(JournalEntry.company_id==Return.company_id, JournalEntry.id==Return.journal_entry_id))
        .join(Revision, and_(Revision.company_id==Return.company_id, Revision.replacement_calculation_id==Return.payroll_calculation_id))
        .join(Application, and_(Application.company_id==Revision.company_id, Application.id==Revision.application_id))
        .where(Return.company_id==company_id, Return.payroll_calculation_id.in_(settlement_ids),
            Return.kind=='employee', Application.source_kind=='severance', Revision.net_delta<0,
            JournalEntry.status=='posted', JournalEntry.entry_date<=payment.payment_date)
        .order_by(JournalEntry.entry_date, JournalEntry.id))).all()
    historical_caps = {}

    async def historical_cap(calculation_id):
        if calculation_id not in historical_caps:
            from app.models.payroll_severance_statutory_record import PayrollSeveranceStatutoryRecord
            from app.services.payroll_severance_calculated_financial import load_calculated_severance_financial
            from app.services.payroll_severance_trusted_classification import TRUSTED_POLICIES
            record = await db.scalar(select(PayrollSeveranceStatutoryRecord).where(
                PayrollSeveranceStatutoryRecord.company_id==company_id,
                PayrollSeveranceStatutoryRecord.payroll_calculation_id==calculation_id))
            historical_caps[calculation_id] = (await load_calculated_severance_financial(
                db,company_id=company_id,calculation_id=calculation_id,
                income_date=record.income_date,policies=TRUSTED_POLICIES,historical=True)).net if record else Decimal(0)
        return historical_caps[calculation_id]

    events = [(prior.payment_date, journal_id or 0, 'payment', (prior,status,reversed_on))
              for prior,status,reversed_on,journal_id in rows]
    for returned,day,revision in returns:
        # A payroll net difference can also contain ordinary-income tax
        # changes. Restore only the actual severance component's decrease.
        ceiling = max(Decimal(0), await historical_cap(revision.original_calculation_id)
                      - await historical_cap(revision.replacement_calculation_id))
        events.append((day,returned.journal_entry_id,'return',(returned,ceiling)))
    consumed = Decimal('0.00')
    refunded = {}
    for _,_,kind,event in sorted(events,key=lambda item:(item[0],item[1])):
        if kind=='return':
            returned,ceiling=event
            already=refunded.get(returned.payroll_calculation_id,Decimal(0))
            restored=min(returned.amount,max(Decimal(0),ceiling-already),consumed)
            refunded[returned.payroll_calculation_id]=already+restored
            consumed-=restored
            continue
        prior,status,reversed_on=event
        if status == 'reversed' and reversed_on is not None and reversed_on <= payment.payment_date:
            continue
        if status is None or status == 'draft':
            raise ValueError('Post or cancel the earlier severance payment reservation first')
        if prior.payment_date > payment.payment_date:
            raise ValueError('Severance payment cannot predate an earlier reserved payment')
        if prior.payroll_calculation_id==payment.payroll_calculation_id:
            prior_cap=protected
        else:
            prior_cap=await historical_cap(prior.payroll_calculation_id)
        consumed += min(prior.amount+prior.withheld_amount,max(Decimal(0),prior_cap-consumed))
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
