"""Replacement monthly salary when ordinary incapacity starts on the hire date."""
from datetime import timedelta
from decimal import Decimal as D
from sqlalchemy import select, or_
from app.models.employee_salary_rate import EmployeeSalaryRate
from app.services.employment_event_service import employment_state_on
from app.services.payroll_sick_leave_service import PayrollSickLeaveDerivationError


async def first_day_salary_basis(db, *, company_id, contract, case_start, limited):
    state = await employment_state_on(db, company_id=company_id, contract_id=contract.id, on_date=case_start)
    if not state['employed'] or state['terms']['start_date'] != case_start.isoformat():
        raise PayrollSickLeaveDerivationError('Replacement salary requires incapacity on the employment start date')
    arrangement = state['terms']['work_arrangement']
    if arrangement not in {'full_time', 'part_time'}:
        raise PayrollSickLeaveDerivationError('A known contractual work arrangement is required')
    # Monthly rates in this ERP are the employee's contractual amount for
    # their own scheduled month (_derive_monthly_salary_amount). A part-time
    # rate already represents the agreed portion; do not scale it a second time.
    rates = list((await db.scalars(select(EmployeeSalaryRate).where(
        EmployeeSalaryRate.company_id == company_id,
        EmployeeSalaryRate.employment_contract_id == contract.id,
        EmployeeSalaryRate.effective_from <= case_start,
        or_(EmployeeSalaryRate.effective_to.is_(None), EmployeeSalaryRate.effective_to >= case_start)))).all())
    if len(rates) != 1 or rates[0].rate_type != 'monthly' or rates[0].currency_code != 'UAH':
        raise PayrollSickLeaveDerivationError('First-day benefit requires one effective monthly UAH salary rate')
    rate = rates[0]
    # January crosses the known 2025/2026 maximum-base boundary. Until that
    # no-reference-period edge is verified, accept only amounts for which
    # either maximum gives exactly the same daily and monthly result.
    if case_start.month == 1 and not limited and rate.amount / D('30.44') * 31 > D('160000'):
        raise PayrollSickLeaveDerivationError('High first-day January benefit requires verified maximum-base treatment')
    limit_reference_end = case_start.replace(day=1) - timedelta(days=1)
    return dict(reference_period_start=None, reference_period_end=None,
        limit_reference_end=limit_reference_end,
        eligible_earnings=rate.amount, eligible_days=D('30.44'),
        basis_method='first_day_monthly_salary',
        sources=[dict(source_type='replacement_salary_rate', source_id=rate.id,
            source_period_start=None, source_period_end=None,
            earnings_amount=rate.amount, eligible_days=D('30.44'),
            source_reference=f'Contractual {arrangement} monthly payable salary at {case_start.isoformat()}; divisor 30.44')])
