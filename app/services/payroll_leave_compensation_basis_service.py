"""Derive compensation averages from actual, non-duplicated payroll sources."""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from sqlalchemy import select
from app.models.employment_contract import EmploymentContract
from app.models.payroll import PayrollCalculation, PayrollPeriod
from app.models.payroll_opening import PayrollEarningsHistory
from app.services.payroll_average_history_service import (
    PayrollAverageHistoryError, _erp_vacation_source, next_month,
)
from app.services.payroll_revision_service import current_calculation_filter
from app.services.payroll_reference_period_service import vacation_employment_month_start


async def derive_compensation_average(db, *, company_id, employment_contract_id,
                                      compensation_date, acquisition_bucket, salary_substitution=None):
    if acquisition_bucket not in ('before_2024','from_2024'):
        raise PayrollAverageHistoryError('Unknown acquisition bucket')
    contract=await db.scalar(select(EmploymentContract).where(EmploymentContract.company_id==company_id,
        EmploymentContract.id==employment_contract_id))
    if contract is None or contract.status=='cancelled' or compensation_date<contract.start_date:
        raise PayrollAverageHistoryError('Valid employment contract required')
    preceding=compensation_date.replace(day=1)-timedelta(days=1)
    if acquisition_bucket=='before_2024':
        if contract.start_date>date(2023,12,31):
            raise PayrollAverageHistoryError('Pre-2024 rights cannot arise after 2023')
        earliest=date(2023,1,1)
        end=min(date(2023,12,31),preceding)
    else:
        earliest=date(compensation_date.year-1,compensation_date.month,1)
        end=preceding
    hire_month=await vacation_employment_month_start(db,company_id=company_id,contract=contract) if contract.start_date>earliest else earliest
    start=max(earliest,hire_month)
    if start>end:
        return await _salary_substitution_basis(db, company_id=company_id, contract=contract,
            compensation_date=compensation_date, start=earliest, end=end,
            context=salary_substitution, evidence=[])
    histories=list((await db.scalars(select(PayrollEarningsHistory).where(
        PayrollEarningsHistory.company_id==company_id,
        PayrollEarningsHistory.employment_contract_id==contract.id,
        PayrollEarningsHistory.month>=start,PayrollEarningsHistory.month<=end))).all())
    if len({row.cutover_date for row in histories})>1:
        raise PayrollAverageHistoryError('Historical sources have inconsistent cutover dates')
    imported={row.month:row for row in histories}
    calculations=(await db.execute(select(PayrollCalculation,PayrollPeriod).join(PayrollPeriod,
        (PayrollPeriod.company_id==PayrollCalculation.company_id)&(PayrollPeriod.id==PayrollCalculation.payroll_period_id))
        .where(PayrollCalculation.company_id==company_id,PayrollCalculation.employment_contract_id==contract.id,
            PayrollPeriod.start_date<=end,PayrollPeriod.end_date>=start,current_calculation_filter()))).all()
    sources=[];month=start
    while month<=end:
        last=month.replace(day=monthrange(month.year,month.month)[1])
        matches=[(c,p) for c,p in calculations if p.start_date<=last and p.end_date>=month]
        imported_row=imported.get(month)
        if imported_row is not None:
            if matches or last>=imported_row.cutover_date:
                raise PayrollAverageHistoryError('Historical month overlaps ERP or cutover')
            earnings=Decimal(imported_row.vacation_earnings);days=Decimal(imported_row.vacation_days)
            if not earnings.is_finite() or earnings<0 or not days.is_finite() or days<0 or days>last.day or (earnings and not days) or not imported_row.source_reference.strip():
                raise PayrollAverageHistoryError('Invalid confirmed compensation history')
            sources.append(dict(source_type='earnings_history',source_id=imported_row.id,
                source_period_start=month,source_period_end=last,earnings_amount=earnings,
                eligible_days=days,source_reference=imported_row.source_reference))
        else:
            if len(matches)!=1:
                raise PayrollAverageHistoryError('Missing or ambiguous compensation history: '+month.isoformat())
            calculation,period=matches[0]
            if histories and month<histories[0].cutover_date:
                raise PayrollAverageHistoryError('ERP source precedes historical cutover')
            sources.append(await _erp_vacation_source(db,company_id=company_id,contract_id=contract.id,
                calculation=calculation,period=period,month=month,month_end=last,purpose='vacation'))
        month=next_month(month)
    earnings=sum((s['earnings_amount'] for s in sources),Decimal(0))
    days=sum((s['eligible_days'] for s in sources),Decimal(0))
    if earnings<=0 or days<=0:
        return await _salary_substitution_basis(db, company_id=company_id, contract=contract,
            compensation_date=compensation_date, start=earliest, end=end,
            context=salary_substitution, evidence=sources)
    return dict(reference_period_start=start,reference_period_end=end,
        eligible_earnings=earnings,eligible_days=days,sources=sources,
        rule_code='UA_ORDER_100_COMPENSATION',rule_version='2023-split-v1')


async def _salary_substitution_basis(db, *, company_id, contract, compensation_date,
                                     start, end, context, evidence):
    """Order 100 paragraph 4: salary substitution only after proving zero earnings.

    Missing historical months never reach this function. The documentary fraction
    establishes the minimum guarantee; the actual salary always comes from ERP.
    """
    from sqlalchemy import or_
    from app.models.employee_salary_rate import EmployeeSalaryRate
    from app.services.payroll_average_history_service import CALENDAR_RULE_START, CALENDAR_RULE_END
    if context is None:
        raise PayrollAverageHistoryError('Salary substitution requires documented employment fraction')
    if not date(2026,1,1) <= compensation_date <= date(2026,12,31):
        raise PayrollAverageHistoryError('Salary substitution requires a verified dated minimum-wage rule')
    if not CALENDAR_RULE_START <= start <= end <= CALENDAR_RULE_END:
        raise PayrollAverageHistoryError('Salary substitution requires a verified reference calendar')
    rates = list((await db.scalars(select(EmployeeSalaryRate).where(
        EmployeeSalaryRate.company_id == company_id,
        EmployeeSalaryRate.employment_contract_id == contract.id,
        EmployeeSalaryRate.effective_from <= compensation_date,
        or_(EmployeeSalaryRate.effective_to.is_(None), EmployeeSalaryRate.effective_to >= compensation_date),
    ))).all())
    if len(rates) != 1 or rates[0].rate_type != 'monthly' or rates[0].currency_code != 'UAH':
        raise PayrollAverageHistoryError('Salary substitution requires one current monthly UAH salary')
    salary = Decimal(rates[0].amount)
    if not salary.is_finite() or salary <= 0:
        raise PayrollAverageHistoryError('Invalid contractual salary')
    monthly_basis = max(salary, Decimal('8647') * context.employment_fraction)
    months = (end.year-start.year)*12 + end.month-start.month + 1
    days = Decimal((end-start).days+1)
    earnings = monthly_basis * months
    source = dict(source_type='salary_substitution', source_id=rates[0].id,
        source_period_start=start, source_period_end=end, earnings_amount=earnings,
        eligible_days=days, source_reference=context.source_reference,
        salary_amount=salary, employment_fraction=context.employment_fraction,
        monthly_minimum=Decimal('8647'), monthly_basis=monthly_basis,
        minimum_rule='UA_LAW_4695_IX_2026', zero_earnings_evidence=evidence)
    return dict(reference_period_start=start, reference_period_end=end,
        eligible_earnings=earnings, eligible_days=days, sources=[source],
        rule_code='UA_ORDER_100_COMPENSATION', rule_version='salary-substitution-v1')
