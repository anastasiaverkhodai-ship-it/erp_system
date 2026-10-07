"""Current calculation means an original or an explicitly linked replacement."""
from sqlalchemy import select, or_
from app.models.payroll import PayrollCalculation
from app.models.payroll_correction import PayrollCorrection


def current_calculation_filter(calculation=PayrollCalculation):
    incoming = select(PayrollCorrection.id).where(
        PayrollCorrection.company_id == calculation.company_id,
        PayrollCorrection.replacement_payroll_calculation_id == calculation.id).exists()
    outgoing = select(PayrollCorrection.id).where(
        PayrollCorrection.company_id == calculation.company_id,
        PayrollCorrection.original_payroll_calculation_id == calculation.id).exists()
    return or_(calculation.revision == 1, incoming) & ~outgoing


async def require_current_calculation(db, *, calculation, error_type):
    current = await db.scalar(select(PayrollCalculation.id).where(
        PayrollCalculation.company_id == calculation.company_id,
        PayrollCalculation.id == calculation.id, current_calculation_filter()))
    if current is None:
        raise error_type('Calculation is superseded or its correction is not linked')


async def require_current_journal_calculation(db, *, journal, error_type):
    calculation_id = journal.payroll_calculation_id
    if journal.payroll_disbursement_id is not None:
        from app.models.payroll_disbursement import PayrollDisbursement
        calculation_id = await db.scalar(select(PayrollDisbursement.payroll_calculation_id).where(
            PayrollDisbursement.company_id == journal.company_id,
            PayrollDisbursement.id == journal.payroll_disbursement_id))
    elif journal.payroll_deduction_result_id is not None:
        from app.models.payroll_deduction_result import PayrollDeductionResult
        calculation_id = await db.scalar(select(PayrollDeductionResult.payroll_calculation_id).where(
            PayrollDeductionResult.company_id == journal.company_id,
            PayrollDeductionResult.id == journal.payroll_deduction_result_id))
    if calculation_id is not None:
        calculation = await db.scalar(select(PayrollCalculation).where(
            PayrollCalculation.company_id == journal.company_id,
            PayrollCalculation.id == calculation_id))
        if calculation is None:
            raise error_type('Payroll calculation not found')
        await require_current_calculation(db, calculation=calculation, error_type=error_type)
