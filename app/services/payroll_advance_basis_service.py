"""Derive the first-half gross advance basis from dated payroll sources."""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from app.services.payroll_input_service import (
    PayrollInputError, _require_payroll_period, _require_contract,
    _contract_period_intersection, _derive_monthly_norm,
    _load_schedule_assignments, _load_schedule_days, _load_attendance,
    _assignment_for_day, _scheduled_minutes_for_day, _load_salary_rates,
    _validate_salary_coverage,
)


class PayrollAdvanceBasisError(ValueError):
    pass


def money(value):
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


async def derive_payroll_advance_basis(db, *, company_id, payroll_period_id,
                                      employment_contract_id, advance_percentage):
    """A gross proposal only; taxes and bank payment are separate operations.

    No future attendance is assumed. Attendance must cover scheduled days in
    the first half; the percentage base is planned ordinary pay for the month.
    """
    percentage = Decimal(advance_percentage)
    if not percentage.is_finite() or not 0 < percentage <= 100:
        raise PayrollAdvanceBasisError('Advance percentage must be greater than zero and at most 100')
    try:
        period = await _require_payroll_period(db, company_id=company_id,
                                              payroll_period_id=payroll_period_id)
        contract = await _require_contract(db, company_id=company_id,
                                          employment_contract_id=employment_contract_id)
        if contract.status == 'cancelled':
            raise PayrollAdvanceBasisError('Cancelled employment cannot receive an advance')
        begin, end = _contract_period_intersection(contract, period)
        cutoff = period.start_date.replace(day=15)
        if begin > cutoff:
            raise PayrollAdvanceBasisError('Employment starts after the first half of this month')
        norm = await _derive_monthly_norm(db, company_id=company_id, contract=contract, period=period)
        assignments = await _load_schedule_assignments(db, company_id=company_id,
            employment_contract_id=contract.id, date_from=begin, date_to=end)
        days = await _load_schedule_days(db, company_id=company_id,
            schedule_ids={row.work_schedule_id for row in assignments})
        attendance = await _load_attendance(db, company_id=company_id,
            employment_contract_id=contract.id, date_from=begin, date_to=min(end, cutoff))
        rates = await _load_salary_rates(db, company_id=company_id,
            employment_contract_id=contract.id, date_from=begin, date_to=end)
        slices = _validate_salary_coverage(rates, date_from=begin, date_to=end)
        currencies = {rate.currency_code for rate, _, _ in slices}
        if len(currencies) != 1:
            raise PayrollAdvanceBasisError('Salary slices must use one currency')
        base, minimum = Decimal(0), Decimal(0)
        sources = []
        for rate, lower, upper in slices:
            planned, worked = 0, 0
            attendance_ids = []
            current = lower
            while current <= upper:
                assignment = _assignment_for_day(assignments, current)
                if assignment is None:
                    raise PayrollAdvanceBasisError(f'Missing work schedule on {current}')
                day = days.get((assignment.work_schedule_id, current.isoweekday()))
                scheduled = _scheduled_minutes_for_day(day) if day is not None else 0
                planned += scheduled
                if current <= cutoff:
                    record = attendance.get(current)
                    if scheduled and record is None:
                        raise PayrollAdvanceBasisError(f'Missing attendance on {current}')
                    if record is not None:
                        if record.worked_minutes < 0:
                            raise PayrollAdvanceBasisError('Worked minutes cannot be negative')
                        if rate.rate_type == 'monthly' and record.worked_minutes > scheduled:
                            raise PayrollAdvanceBasisError('Extra work requires a separate earning calculation')
                        worked += record.worked_minutes
                        attendance_ids.append(record.id)
                current += timedelta(days=1)
            denominator = norm if rate.rate_type == 'monthly' else 60
            if rate.rate_type not in ('monthly', 'hourly') or denominator <= 0:
                raise PayrollAdvanceBasisError('Salary rate or monthly norm is invalid')
            slice_base = money(rate.amount * Decimal(planned) / denominator)
            slice_minimum = money(rate.amount * Decimal(worked) / denominator)
            base += slice_base
            minimum += slice_minimum
            sources.append(dict(salary_rate_id=rate.id, effective_from=lower,
                effective_to=upper, rate_type=rate.rate_type, rate_amount=rate.amount,
                planned_minutes=planned, worked_minutes=worked,
                attendance_ids=attendance_ids, base_amount=slice_base,
                minimum_amount=slice_minimum))
        percentage_amount = money(base * percentage / 100)
        return dict(company_id=company_id, payroll_period_id=period.id,
            employment_contract_id=contract.id, earned_through=min(cutoff, end),
            currency_code=currencies.pop(), advance_percentage=percentage,
            monthly_norm_minutes=norm, calculation_base_gross=base,
            percentage_gross=percentage_amount, minimum_gross=minimum,
            recommended_gross=max(percentage_amount, minimum),
            percentage_meets_minimum=percentage_amount >= minimum, sources=sources)
    except PayrollInputError as exc:
        raise PayrollAdvanceBasisError(str(exc)) from exc
