"""Calendar arithmetic for documented leave entitlement periods.

Eligibility, exclusions and annual allowances are source facts. This module does
not infer disability, hazardous work or parental eligibility from job titles.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP


@dataclass(frozen=True)
class EntitlementSegment:
    start: date
    end: date
    annual_days: Decimal


def days_between(start, end):
    if end < start:
        raise ValueError('Invalid period')
    return (start + timedelta(days=i) for i in range((end-start).days + 1))


def annual_entitlement(*, year_start, year_end, through, segments,
                       excluded_dates=(), calendar_exclusions=()):
    """Prorate documented annual rates; preserve pre/post-2023 acquisition.

    The full working year's calendar is the denominator. Service exclusions
    reduce the numerator only. A changed allowance is prorated on its own days.
    No FTE multiplier is applied to leave duration.
    """
    all_days = set(days_between(year_start, year_end))
    if len(all_days) not in (365, 366):
        raise ValueError('A working year must contain 365 or 366 calendar days')
    holidays = set(calendar_exclusions)
    excluded = set(excluded_dates)
    if not holidays <= all_days or not excluded <= all_days:
        raise ValueError('Excluded date falls outside the working year')
    denominator = len(all_days - holidays)
    if not denominator:
        raise ValueError('Empty working year calendar')
    rates = {}
    for segment in segments:
        amount = Decimal(segment.annual_days)
        if not amount.is_finite() or amount <= 0 or amount > 365:
            raise ValueError('Invalid annual allowance')
        segment_days = set(days_between(segment.start, segment.end))
        if not segment_days <= all_days or rates.keys() & segment_days:
            raise ValueError('Overlapping or out-of-year entitlement segments')
        rates.update({day: amount for day in segment_days})
    eligible = {day for day in all_days if day <= through} - excluded - holidays
    if eligible - rates.keys():
        raise ValueError('Missing allowance for eligible dates')
    old = sum((rates[day] for day in eligible if day <= date(2023,12,31)), Decimal(0)) / denominator
    new = sum((rates[day] for day in eligible if day > date(2023,12,31)), Decimal(0)) / denominator
    return {'before_2024': old, 'from_2024': new, 'total': old + new,
            'eligible_days': len(eligible), 'year_days': denominator}


def rounded_entitlement_buckets(values):
    """Round the cumulative right once, without creating a day at a boundary."""
    total = values['total'].quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    old = values['before_2024'].quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    return {'before_2024': old, 'from_2024': total-old, 'total': total}


def compensation_amount(*, days, eligible_earnings, eligible_days):
    days, earnings, denominator = map(Decimal, (days, eligible_earnings, eligible_days))
    if any(not value.is_finite() for value in (days,earnings,denominator)) or days < 0 or earnings < 0 or denominator <= 0:
        raise ValueError('Invalid compensation basis')
    # Avoid rounding the daily quotient before multiplying by the entitlement.
    return (earnings * days / denominator).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
