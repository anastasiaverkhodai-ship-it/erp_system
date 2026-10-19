"""Order 100 bonus allocation with explicit earning-period evidence."""
from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import select, func
from app.models.payroll import PayrollCalculation, PayrollCalculationLine, PayrollPeriod
from app.models.payroll_supplement import PayrollSupplement
from app.models.time_attendance import AttendanceRecord
from app.services.payroll_revision_service import current_calculation_filter


def allocate_bonus(*, amount, worked_days, planned_days, target_worked_days):
    """Keep full precision until the final source total is saved.

    The partial-period cap applies to the sum included in this reference window.
    Monthly detail remains available to explain that cap and its allocation.
    """
    amount = Decimal(amount)
    if not amount.is_finite() or amount <= 0 or not 0 < worked_days <= planned_days:
        raise ValueError('Bonus requires positive amount and documented worked/planned days')
    if any(type(days) is not int or days < 0 for days in target_worked_days.values()):
        raise ValueError('Invalid worked days in bonus allocation month')
    remaining = amount if worked_days < planned_days else None
    result = {}
    for month, days in sorted(target_worked_days.items()):
        raw = amount * days / worked_days
        included = raw if remaining is None else min(raw, remaining)
        result[month] = included
        if remaining is not None:
            remaining -= included
    return result


def allocation_months(accrual_month, earning_start, earning_end):
    count = (earning_end.year - earning_start.year) * 12 + earning_end.month - earning_start.month + 1
    result = []
    for offset in range(count):
        n = accrual_month.year * 12 + accrual_month.month - 1 + offset
        result.append(date(n // 12, n % 12 + 1, 1))
    return result


async def bonus_average_sources(db, *, company_id, contract_id, start, end, sources):
    from app.services.payroll_average_history_service import PayrollAverageHistoryError
    rows = (await db.execute(
        select(PayrollSupplement, PayrollCalculationLine, PayrollCalculation, PayrollPeriod)
        .join(PayrollCalculationLine,
              (PayrollCalculationLine.company_id == PayrollSupplement.company_id) &
              (PayrollCalculationLine.source_supplement_id == PayrollSupplement.id))
        .join(PayrollCalculation,
              (PayrollCalculation.company_id == PayrollCalculationLine.company_id) &
              (PayrollCalculation.id == PayrollCalculationLine.payroll_calculation_id))
        .join(PayrollPeriod,
              (PayrollPeriod.company_id == PayrollCalculation.company_id) &
              (PayrollPeriod.id == PayrollCalculation.payroll_period_id))
        .where(PayrollSupplement.company_id == company_id,
               PayrollSupplement.kind == 'bonus',
               PayrollCalculation.employment_contract_id == contract_id,
               PayrollPeriod.start_date <= end, current_calculation_filter())
    )).all()
    source_months = {source['source_period_start']: source for source in sources}
    results = []
    seen = set()
    for bonus, line, calculation, period in rows:
        if bonus.id in seen:
            raise PayrollAverageHistoryError('Duplicated performance bonus source')
        seen.add(bonus.id)
        evidence = bonus.average_basis
        # Older bonuses without earning dates cannot safely be assumed irrelevant.
        if evidence is None:
            raise PayrollAverageHistoryError('Performance bonus requires allocation over its earning period')
        from app.schemas.payroll_supplement import BonusAverageBasis
        try:
            basis = BonusAverageBasis.model_validate(evidence)
        except ValueError as exc:
            raise PayrollAverageHistoryError('Invalid documented bonus earning basis') from exc
        months = [month for month in allocation_months(period.start_date, basis.period_start, basis.period_end)
                  if start <= month <= end]
        if not months:
            continue
        if (period.status != 'finalized' or period.start_date.day != 1
            or period.end_date.day != monthrange(period.end_date.year, period.end_date.month)[1]
            or period.start_date.replace(day=1) != period.end_date.replace(day=1)
            or bonus.cancelled_at is not None or calculation.currency_code != 'UAH'
            or line.currency_code != 'UAH' or line.line_type != 'supplement'
            or calculation.payroll_input_id != bonus.payroll_input_id
            or Decimal(bonus.amount) <= 0 or Decimal(line.amount) != Decimal(bonus.amount)
            or not period.start_date <= bonus.work_date <= period.end_date
            or basis.period_end > period.end_date):
            raise PayrollAverageHistoryError('Bonus does not reconcile to its finalized payroll source')
        worked = {}
        month_calculations = {}
        for month in months:
            source = source_months.get(month)
            if source is None or source['source_type'] != 'payroll_calculation':
                raise PayrollAverageHistoryError('ERP bonus allocation requires attributed ERP target months; imported totals cannot be adjusted twice')
            last = month.replace(day=monthrange(month.year, month.month)[1])
            worked[month] = int(await db.scalar(select(func.count()).select_from(AttendanceRecord).where(
                AttendanceRecord.company_id == company_id,
                AttendanceRecord.employment_contract_id == contract_id,
                AttendanceRecord.work_date >= month, AttendanceRecord.work_date <= last,
                AttendanceRecord.worked_minutes > 0)) or 0)
            month_calculations[month] = source['source_id']
        amounts = allocate_bonus(amount=bonus.amount, worked_days=basis.worked_days,
                                 planned_days=basis.planned_days, target_worked_days=worked)
        total = sum(amounts.values(), Decimal(0)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        results.append(dict(
            source_type='bonus_allocation', source_id=bonus.id,
            source_period_start=start, source_period_end=end, earnings_amount=total,
            eligible_days=Decimal(0), source_reference=bonus.source_reference,
            basis_snapshot=dict(
                rule='UA_ORDER_100_PARAGRAPH_3', earning_basis=basis.model_dump(mode='json'),
                payroll_calculation_id=calculation.id, amount=str(bonus.amount),
                allocations=[dict(month=month.isoformat(), worked_days=worked[month],
                                  payroll_calculation_id=month_calculations[month],
                                  included_amount=str(value)) for month, value in amounts.items()],
                partial_period_cap=basis.worked_days < basis.planned_days)))
    return results
