"""Confirmed settlement and overpayment evidence; never implicit recovery."""
from decimal import Decimal as D
from sqlalchemy import select, func
from sqlalchemy.orm import aliased, selectinload
from app.models.journal_entry import JournalEntry
from app.models.payroll import PayrollCalculation
from app.models.payroll_statutory import PayrollStatutoryResult
from app.models.payroll_sick_return import PayrollSickReturn
from app.services.payroll_sick_financial_sources import sick_settlement_chain


def effective_journal(as_of):
    reversal = aliased(JournalEntry)
    undone = select(reversal.id).where(reversal.company_id == JournalEntry.company_id,
        reversal.reversal_of_id == JournalEntry.id, reversal.status == 'posted',
        reversal.entry_date <= as_of).exists()
    return (JournalEntry.status.in_(('posted','reversed')),
            JournalEntry.reversal_of_id.is_(None), JournalEntry.entry_date <= as_of, ~undone)


async def confirmed_returns(db, *, company_id, calculation_ids, kind, as_of):
    return D(await db.scalar(select(func.coalesce(func.sum(PayrollSickReturn.amount),0))
        .join(JournalEntry, (JournalEntry.company_id == PayrollSickReturn.company_id) &
              (JournalEntry.id == PayrollSickReturn.journal_entry_id))
        .where(PayrollSickReturn.company_id == company_id,
            PayrollSickReturn.payroll_calculation_id.in_(calculation_ids),
            PayrollSickReturn.kind == kind, *effective_journal(as_of))) or 0)


async def available_return_credit(db, *, company_id, calculation_ids, kind, from_date):
    """Minimum confirmed return from this date onward, including reversals."""
    reversal = aliased(JournalEntry)
    rows = (await db.execute(select(PayrollSickReturn.amount, JournalEntry.entry_date, reversal.entry_date)
        .join(JournalEntry, (JournalEntry.company_id == PayrollSickReturn.company_id) &
              (JournalEntry.id == PayrollSickReturn.journal_entry_id))
        .outerjoin(reversal, (reversal.company_id == JournalEntry.company_id) &
                   (reversal.reversal_of_id == JournalEntry.id) & (reversal.status == 'posted'))
        .where(PayrollSickReturn.company_id == company_id,
            PayrollSickReturn.payroll_calculation_id.in_(calculation_ids), PayrollSickReturn.kind == kind,
            JournalEntry.status.in_(('posted','reversed'))))).all()
    changes = {}
    for amount, day, reversed_on in rows:
        changes[day] = changes.get(day,D(0))+amount
        if reversed_on is not None:
            changes[reversed_on] = changes.get(reversed_on,D(0))-amount
    balance = sum((value for day,value in changes.items() if day <= from_date),D(0))
    minimum = balance
    for day in sorted(day for day in changes if day > from_date):
        balance += changes[day]
        minimum = min(minimum,balance)
    return minimum


async def sick_settlement_summary(db, *, company_id, calculation_id, as_of):
    from app.models.payroll_disbursement import PayrollDisbursement
    from app.models.payroll_advance import PayrollAdvance
    from app.models.payroll_deduction_result import PayrollDeductionResult
    from app.models.payroll_insurance_funding import PayrollInsuranceFunding, PayrollInsuranceFundingLine
    from app.services.payroll_sick_financial_sources import load_financial_snapshot
    from app.services.payroll_sick_correction_plan import plan_sick_correction
    ids = await sick_settlement_chain(db, company_id=company_id, calculation_id=calculation_id)
    calc = await db.scalar(select(PayrollCalculation).where(
        PayrollCalculation.company_id == company_id, PayrollCalculation.id == calculation_id))
    from app.services.payroll_deduction_result_service import require_authorized_deduction_result
    deductions = await db.scalars(select(PayrollDeductionResult).where(
        PayrollDeductionResult.company_id==company_id,
        PayrollDeductionResult.payroll_calculation_id.in_(ids)))
    for deduction in deductions:
        require_authorized_deduction_result(deduction)
    snapshot = await load_financial_snapshot(db, company_id=company_id, calculation_id=calculation_id)
    paid = D(await db.scalar(select(func.coalesce(func.sum(
        PayrollDisbursement.amount+PayrollDisbursement.withheld_amount),0)).where(
        PayrollDisbursement.company_id == company_id,
        PayrollDisbursement.payroll_calculation_id.in_(ids),
        PayrollDisbursement.confirmed_at.is_not(None), PayrollDisbursement.payment_date <= as_of,
        (PayrollDisbursement.reversed_on.is_(None)) | (PayrollDisbursement.reversed_on > as_of))) or 0)
    for model, journal_source, amount, condition in (
        (PayrollAdvance, JournalEntry.payroll_advance_id, PayrollAdvance.paid_amount+PayrollAdvance.withheld_amount,
         (PayrollAdvance.payroll_period_id==calc.payroll_period_id) &
         (PayrollAdvance.employment_contract_id==calc.employment_contract_id)),
        (PayrollDeductionResult, JournalEntry.payroll_deduction_result_id, PayrollDeductionResult.deduction_amount,
         PayrollDeductionResult.payroll_calculation_id.in_(ids))):
        paid += D(await db.scalar(select(func.coalesce(func.sum(amount),0)).join(JournalEntry,
            (JournalEntry.company_id==model.company_id) & (journal_source==model.id))
            .where(model.company_id==company_id, condition, *effective_journal(as_of))) or 0)
    funding = D(await db.scalar(select(func.coalesce(func.sum(PayrollInsuranceFundingLine.amount),0))
        .join(PayrollInsuranceFunding,
            (PayrollInsuranceFunding.company_id==PayrollInsuranceFundingLine.company_id) &
            (PayrollInsuranceFunding.id==PayrollInsuranceFundingLine.funding_id))
        .join(JournalEntry, (JournalEntry.company_id==PayrollInsuranceFunding.company_id) &
            (JournalEntry.id==PayrollInsuranceFunding.journal_entry_id))
        .where(PayrollInsuranceFundingLine.company_id==company_id,
            PayrollInsuranceFundingLine.payroll_calculation_id.in_(ids), *effective_journal(as_of))) or 0)
    employee_returned = await confirmed_returns(db, company_id=company_id, calculation_ids=ids,
        kind='employee', as_of=as_of)
    insurer_returned = await confirmed_returns(db, company_id=company_id, calculation_ids=ids,
        kind='insurer', as_of=as_of)
    result = plan_sick_correction(original=snapshot, replacement=snapshot,
        employee_settled=paid, insurer_received=funding,
        employee_returned=employee_returned, insurer_returned=insurer_returned)
    return dict(payroll_calculation_id=calculation_id, as_of=as_of, employee_settled=paid,
        employee_returned=employee_returned, employee_due=result.employee_due,
        employee_overpayment=result.employee_overpayment, insurer_received=funding,
        insurer_returned=insurer_returned, insurer_due=result.insurer_funding_due,
        insurer_excess=result.insurer_funding_excess)
