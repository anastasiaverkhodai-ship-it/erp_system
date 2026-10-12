"""Dated ordinary sick-pay ceilings and an auditable daily allocation."""
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from app.services.payroll_leave_allocation import allocate_leave_amount

D = Decimal
ZERO = D('0.00')
ALLOCATION_VERSION = '2-2026-monthly-ceilings'
FIRST_DAY_VERSION = '3-2026-first-day-monthly'
DATED_ALLOCATION_VERSIONS = {ALLOCATION_VERSION, FIRST_DAY_VERSION}


def money(value):
    return D(value).quantize(D('.01'), rounding=ROUND_HALF_UP)


def maximum_contribution_base(day):
    if day.year == 2025:
        return D('160000.00')
    if day.year == 2026:
        return D('172940.00')
    raise ValueError('No verified ordinary sick-pay maximum for this reference year')


def daily_benefit(*, earnings, days, percent, reference_end, limited):
    if D(days) <= 0 or D(earnings) < 0 or not 0 < D(percent) <= 100:
        raise ValueError('Invalid sick-pay basis or percentage')
    maximum = maximum_contribution_base(reference_end)
    capped_average = min(D(earnings) / D(days), money(maximum / D('30.44')))
    daily = money(capped_average * D(percent) / 100)
    if limited:
        daily = min(daily, money(D('8647') / D('30.44')))
    return daily, maximum


def allocate_sick_days(*, start, end, case_start, daily, maximum, previous=None):
    """Consume each calendar month's remaining ceiling in date order.

    Earlier certificates remain unchanged. All money is assigned to a date,
    so partial payroll periods cannot redistribute a capped month's amount.
    """
    if not case_start <= start <= end or daily < 0 or maximum <= 0:
        raise ValueError('Invalid sick-pay allocation interval or limit')
    spent = {key: D(value) for key, value in (previous or {}).items()}
    if any(not value.is_finite() or value < 0 for value in spent.values()):
        raise ValueError('Invalid prior monthly sick-pay consumption')
    result = []
    current = start
    while current <= end:
        key = current.strftime('%Y-%m')
        amount = min(money(daily), max(ZERO, maximum - spent.get(key, ZERO)))
        spent[key] = spent.get(key, ZERO) + amount
        result.append(dict(date=current.isoformat(),
            payer='employer' if current < case_start + timedelta(days=5) else 'insurer',
            amount=str(money(amount))))
        current += timedelta(days=1)
    return result


def sick_period_amount(row, leave, period_start, period_end):
    """Use saved dated amounts; legacy calculations retain their old allocation."""
    assessment = row.insurance_assessment or {}
    if row.rule_version not in DATED_ALLOCATION_VERSIONS:
        return allocate_leave_amount(start=leave.start_date, end=leave.end_date,
            period_start=period_start, period_end=period_end, amount=row.sick_pay_amount)
    entries = assessment.get('daily_allocation')
    if not isinstance(entries, list) or len(entries) != (leave.end_date - leave.start_date).days + 1:
        raise ValueError('Sick-pay daily allocation is missing or incomplete')
    totals = {'employer': ZERO, 'insurer': ZERO}
    amount, count = ZERO, 0
    for offset, entry in enumerate(entries):
        day = leave.start_date + timedelta(days=offset)
        value = D(entry['amount'])
        if (entry['date'] != day.isoformat() or entry['payer'] not in totals
                or not value.is_finite() or value < 0 or money(value) != value):
            raise ValueError('Invalid saved sick-pay daily allocation')
        totals[entry['payer']] += value
        if period_start <= day <= period_end:
            amount += value
            count += 1
    if (totals['employer'] != row.employer_amount or totals['insurer'] != row.insurer_amount
            or sum(totals.values()) != row.sick_pay_amount):
        raise ValueError('Sick-pay daily allocation does not reconcile to financing totals')
    return count, amount
