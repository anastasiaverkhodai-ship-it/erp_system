"""Allocate a fixed leave entitlement across periods without repeating cents."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP


def allocate_leave_amount(*, start: date, end: date, period_start: date,
                          period_end: date, amount: Decimal):
    if end < start or period_end < period_start:
        raise ValueError('Invalid leave or payroll date range')
    lower, upper = max(start, period_start), min(end, period_end)
    if lower > upper:
        return 0, Decimal('0.00')
    total_days = (end - start).days + 1
    before = (lower - start).days
    through = (upper - start).days + 1
    # Differences of cumulative rounded totals make adjacent periods telescope
    # to exactly the stored entitlement, even when a daily amount has fractions.
    def cumulative(days):
        return (Decimal(amount) * days / total_days).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    return through - before, cumulative(through) - cumulative(before)
