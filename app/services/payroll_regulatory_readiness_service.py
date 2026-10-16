"""Read actual payroll sources without claiming a statutory form is complete."""
from sqlalchemy import and_, select
from app.models.employee import Employee
from app.models.employment_contract import EmploymentContract
from app.models.payroll import PayrollCalculation, PayrollPeriodStatus
from app.models.payroll_statutory import PayrollStatutoryResult, PayrollStatutoryResultLine
from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.payroll_revision_service import current_calculation_filter
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError, _get_period, _kind_value,
)

# These are implementation gaps, not a declaration that a company has no duty
# to submit an annex. In particular, an empty source set cannot prove D6 N/A.
FORM_BLOCKERS = {
    'd1': ['D1_CATEGORY_AND_INCOME_MONTH_ALLOCATION_MISSING'],
    '4df': ['FOUR_DF_GROSS_PAYMENT_AND_TAX_ALLOCATION_MISSING'],
    'd5': ['D5_PROFESSIONAL_DETAILS_AND_EVENT_MAPPING_MISSING'],
    'd6': ['D6_APPLICABILITY_AND_SPECIAL_SERVICE_EVIDENCE_MISSING'],
}


@serialized_payroll_mutation(PayrollRegulatoryReportError)
async def payroll_regulatory_readiness(db, *, company_id, payroll_period_id, report_kind):
    period = await _get_period(db, company_id=company_id, payroll_period_id=payroll_period_id)
    kind = _kind_value(report_kind)
    blockers = ['DATED_STATUTORY_FORM_MAPPING_MISSING', *FORM_BLOCKERS[kind]]
    if period.status != PayrollPeriodStatus.FINALIZED:
        blockers.append('PAYROLL_PERIOD_NOT_FINALIZED')
    records = (await db.execute(
        select(PayrollCalculation, EmploymentContract, Employee, PayrollStatutoryResult)
        .join(EmploymentContract, and_(
            EmploymentContract.company_id == PayrollCalculation.company_id,
            EmploymentContract.id == PayrollCalculation.employment_contract_id))
        .join(Employee, and_(Employee.company_id == EmploymentContract.company_id,
                            Employee.id == EmploymentContract.employee_id))
        .outerjoin(PayrollStatutoryResult, and_(
            PayrollStatutoryResult.company_id == PayrollCalculation.company_id,
            PayrollStatutoryResult.payroll_calculation_id == PayrollCalculation.id))
        .where(PayrollCalculation.company_id == company_id,
               PayrollCalculation.payroll_period_id == payroll_period_id,
               current_calculation_filter())
        .order_by(Employee.id, EmploymentContract.id, PayrollCalculation.id)
    )).all()
    result_ids = [statutory.id for _, _, _, statutory in records if statutory is not None]
    components = {}
    if result_ids:
        lines = (await db.scalars(select(PayrollStatutoryResultLine).where(
            PayrollStatutoryResultLine.company_id == company_id,
            PayrollStatutoryResultLine.payroll_statutory_result_id.in_(result_ids)
        ).order_by(PayrollStatutoryResultLine.line_no))).all()
        for line in lines:
            components.setdefault(line.payroll_statutory_result_id, []).append({
                'source_line_id': line.id, 'component': line.component,
                'base_amount': line.base_amount, 'rate': line.rate,
                'amount': line.amount, 'currency_code': line.currency_code,
            })
    rows = []
    for calculation, contract, employee, statutory in records:
        issues = []
        if not employee.tax_number:
            issues.append('EMPLOYEE_TAX_ID_OR_CONFIRMED_ALTERNATIVE_MISSING')
        if statutory is None:
            issues.append('STATUTORY_CALCULATION_MISSING')
        rows.append({
            'employee_id': employee.id, 'employment_contract_id': contract.id,
            'payroll_calculation_id': calculation.id, 'revision': calculation.revision,
            'tax_number': employee.tax_number, 'issues': issues,
            'gross_amount': statutory.gross_amount if statutory else None,
            'net_amount': statutory.net_amount if statutory else None,
            'employee_withholding_amount': statutory.employee_withholding_amount if statutory else None,
            'employer_contribution_amount': statutory.employer_contribution_amount if statutory else None,
            'currency_code': statutory.currency_code if statutory else None,
            'components': components.get(statutory.id, []) if statutory else [],
        })
    return {
        'company_id': company_id, 'payroll_period_id': payroll_period_id,
        'report_kind': kind, 'ready': False, 'applicability': 'not_determined',
        'blocking_issues': blockers, 'calculation_sources': rows,
        'notice': 'Джерела для перевірки. Це не сформований регламентований звіт.',
    }
