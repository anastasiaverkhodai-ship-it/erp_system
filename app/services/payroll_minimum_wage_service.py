"""Minimum-wage arithmetic with explicit ordinary-time and earning classes.

No statutory amount is guessed here. The caller supplies a dated monthly/hourly
minimum and the full-time norm. Ordinary paid work is separated from overtime,
paid absences and reimbursements before comparing earnings to the guarantee.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


class MinimumWageError(ValueError):
    pass


@dataclass(frozen=True)
class MinimumWageEarning:
    category: str
    amount: Decimal


@dataclass(frozen=True)
class MinimumWageAssessment:
    guaranteed_amount: Decimal
    included_earnings: Decimal
    excluded_earnings: Decimal
    top_up: Decimal


INCLUDED = frozenset({'ordinary_salary', 'production_bonus', 'regular_allowance', 'indexation'})
EXCLUDED = frozenset({'night', 'overtime', 'hazardous', 'travelling', 'holiday_bonus',
                      'jubilee_bonus', 'vacation_pay', 'sick_pay', 'hardship', 'reimbursement'})


def _decimal(value, name):
    try:
        result = Decimal(value)
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise MinimumWageError(f'Invalid {name}') from exc
    if not result.is_finite() or result < 0:
        raise MinimumWageError(f'{name} must be finite and nonnegative')
    return result


def assess_minimum_wage(*, rate_type, minimum_rate, ordinary_worked_minutes,
                        full_time_norm_minutes, earnings):
    """Assess one homogeneous monthly/hourly segment; round money only at the end.

    The norm is full-time, not an individual's reduced part-time schedule.
    Overtime and rest-day time cannot silently increase ordinary worked time.
    Input amounts are gross earnings, before taxes and employee deductions.
    """
    rate = _decimal(minimum_rate, 'minimum rate')
    if rate == 0:
        raise MinimumWageError('Minimum rate must be positive')
    if not isinstance(ordinary_worked_minutes, int) or isinstance(ordinary_worked_minutes, bool) or ordinary_worked_minutes < 0:
        raise MinimumWageError('Ordinary worked minutes must be a nonnegative integer')
    if rate_type == 'monthly':
        if (not isinstance(full_time_norm_minutes, int) or isinstance(full_time_norm_minutes, bool)
                or full_time_norm_minutes <= 0):
            raise MinimumWageError('Monthly guarantee requires a positive full-time norm')
        if ordinary_worked_minutes > full_time_norm_minutes:
            raise MinimumWageError('Classify work exceeding the full-time norm separately')
        guarantee = rate * ordinary_worked_minutes / full_time_norm_minutes
    elif rate_type == 'hourly':
        guarantee = rate * ordinary_worked_minutes / 60
    else:
        raise MinimumWageError('Unknown salary rate type')
    included = excluded = Decimal(0)
    for row in earnings:
        amount = _decimal(row.amount, 'earning amount')
        if amount != amount.quantize(Decimal('.01')):
            raise MinimumWageError('Earnings must have cent precision')
        if row.category in INCLUDED:
            included += amount
        elif row.category in EXCLUDED:
            excluded += amount
        else:
            raise MinimumWageError('Unclassified earning: ' + str(row.category))
    guarantee = guarantee.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    return MinimumWageAssessment(guarantee, included, excluded, max(Decimal('0.00'), guarantee-included))


def normalize_minimum_wage_context(context):
    from pydantic import ValidationError
    from app.schemas.payroll_calculation import PayrollMinimumWageContext
    try:
        return PayrollMinimumWageContext.model_validate(context or {}).model_dump(exclude_none=True)
    except ValidationError as exc:
        raise MinimumWageError('Invalid minimum-wage context: ' + str(exc)) from exc


async def derive_minimum_wage_assessment(db, *, company_id, period, payroll_input,
                                        salary_slices, lines, context=None):
    """Build a saved 2026 UAH assessment from attributed payroll earnings."""
    from datetime import date
    from sqlalchemy import select
    from app.models.employment_contract import EmploymentContract
    from app.models.payroll_supplement import PayrollSupplement
    from app.services.employment_event_service import employment_state_on, list_employment_events
    context = normalize_minimum_wage_context(context)
    if any(line['currency_code'] != 'UAH' for line in lines):
        return {'status': 'requires_uah_conversion', 'request': context}, None
    if not date(2026,1,1) <= period.start_date <= period.end_date <= date(2026,12,31):
        raise MinimumWageError('Minimum wage requires a verified dated rule for this period')
    types = {str(getattr(s.salary_rate_type,'value',s.salary_rate_type)) for s in salary_slices}
    if len(types) != 1:
        raise MinimumWageError('Mixed salary methods require separate guarantee segments')
    rate_type = next(iter(types))
    norm = context.get('full_time_norm_minutes')
    contract = await db.scalar(select(EmploymentContract).where(
        EmploymentContract.company_id == company_id, EmploymentContract.id == payroll_input.employment_contract_id))
    if contract is None:
        raise MinimumWageError('Employment contract not found')
    if rate_type == 'monthly' and norm is None:
        # A reduced schedule is not proof of the applicable full-time norm.
        dates = {part.effective_from for part in salary_slices}
        events = await list_employment_events(db, company_id=company_id, contract_id=contract.id)
        dates.update(event.effective_date for event in events
                     if period.start_date <= event.effective_date <= period.end_date)
        for on_date in sorted(dates):
            state = await employment_state_on(db, company_id=company_id,
                contract_id=contract.id, on_date=on_date)
            if state['terms']['work_arrangement'] != 'full_time':
                raise MinimumWageError('Part-time monthly guarantee requires the documented full-time norm')
        norm = payroll_input.monthly_norm_minutes
    if context.get('full_time_norm_minutes') is not None and not context.get('norm_reference','').strip():
        raise MinimumWageError('Explicit full-time norm requires a documentary reference')
    worked = sum(s.worked_minutes for s in salary_slices)
    extra_by_slice = {}
    earnings = []
    adjustment = Decimal(0)
    supplements = {s.id:s for s in (await db.scalars(select(PayrollSupplement).where(
        PayrollSupplement.company_id == company_id,
        PayrollSupplement.payroll_input_id == payroll_input.id,
        PayrollSupplement.cancelled_at.is_(None)))).all()}
    for line in lines:
        kind = line['line_type']
        if kind == 'supplement':
            source = supplements.get(line.get('source_supplement_id'))
            if source is None:
                raise MinimumWageError('Missing supplement source for minimum guarantee')
            category = {'bonus':'production_bonus','holiday_bonus':'holiday_bonus','hardship':'hardship',
                        'night':'night','overtime':'overtime','regular_extra':'ordinary_salary'}.get(source.kind)
            if category is None:
                raise MinimumWageError('Rest-day compensation requires explicit minimum-wage classification')
            earnings.append(MinimumWageEarning(category,Decimal(line['amount'])))
            if source.kind == 'overtime':
                parts = [s for s in salary_slices if s.effective_from <= source.work_date <= s.effective_to]
                if len(parts) != 1:
                    raise MinimumWageError('Overtime requires exactly one salary snapshot')
                extra_by_slice[parts[0].id] = extra_by_slice.get(parts[0].id,0) + source.minutes
        elif kind in {'vacation_pay','sick_pay'}:
            earnings.append(MinimumWageEarning(kind,Decimal(line['amount'])))
        elif kind == 'manual_adjustment':
            if not context.get('manual_adjustment_category'):
                raise MinimumWageError('Manual adjustment requires an explicit minimum-wage classification')
            adjustment += Decimal(line['amount'])
        elif kind != 'salary':
            raise MinimumWageError('Unclassified earning line')
    salary_lines = [line for line in lines if line['line_type']=='salary']
    if len(salary_lines) != len(salary_slices):
        raise MinimumWageError('Salary lines do not match salary snapshots')
    for part,line in zip(salary_slices,salary_lines):
        extra = extra_by_slice.get(part.id,0)
        if extra > part.worked_minutes:
            raise MinimumWageError('Overtime exceeds salary time')
        if rate_type == 'monthly':
            if not payroll_input.monthly_norm_minutes or payroll_input.monthly_norm_minutes <= 0:
                raise MinimumWageError('Monthly salary requires its original norm')
            extra_pay = Decimal(part.salary_rate_amount)*extra/payroll_input.monthly_norm_minutes
        else:
            extra_pay = Decimal(part.salary_rate_amount)*extra/60
        extra_pay = extra_pay.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        earnings.append(MinimumWageEarning('ordinary_salary',Decimal(line['amount'])-extra_pay))
        earnings.append(MinimumWageEarning('overtime',extra_pay))
        worked -= extra
    if context.get('manual_adjustment_category'):
        if not any(line['line_type'] == 'manual_adjustment' for line in lines):
            raise MinimumWageError('Adjustment classification has no matching earning line')
        totals = {}
        for earning in earnings:
            totals[earning.category] = totals.get(earning.category, Decimal(0)) + earning.amount
        category = context['manual_adjustment_category']
        totals[category] = totals.get(category, Decimal(0)) + adjustment
        if any(amount < 0 for amount in totals.values()):
            raise MinimumWageError('Adjustment exceeds the current-period earnings in its category; use a correction')
        earnings = [MinimumWageEarning(category, amount) for category, amount in totals.items()]
    rate = Decimal('8647') if rate_type=='monthly' else Decimal('52')
    result = assess_minimum_wage(rate_type=rate_type,minimum_rate=rate,
        ordinary_worked_minutes=worked,full_time_norm_minutes=norm,earnings=earnings)
    snapshot = dict(status='assessed',rule_version='UA-4695-IX-2026-v1',
        source_reference='https://zakon.rada.gov.ua/laws/show/4695-IX',
        rate_type=rate_type,minimum_rate=str(rate),ordinary_worked_minutes=worked,
        full_time_norm_minutes=norm,guaranteed_amount=str(result.guaranteed_amount),
        included_earnings=str(result.included_earnings),excluded_earnings=str(result.excluded_earnings),
        top_up=str(result.top_up),request=context)
    line = None
    if result.top_up:
        line = dict(line_type='minimum_wage',description='Minimum-wage top-up; UA-4695-IX-2026-v1',
            quantity=Decimal(1),rate=result.top_up,amount=result.top_up,currency_code='UAH',
            source_effective_from=period.start_date,source_effective_to=period.end_date)
    return snapshot,line
