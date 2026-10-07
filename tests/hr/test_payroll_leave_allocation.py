from datetime import date
from decimal import Decimal

import pytest

from app.services.payroll_leave_allocation import allocate_leave_amount


@pytest.mark.parametrize('amount', ['0.01', '100.00', '1234.57'])
def test_three_month_allocation_preserves_every_cent(amount):
    # Leap day and unequal months must not repeat or lose rounded cents.
    intervals = [(date(2024, 1, 1), date(2024, 1, 31)),
                 (date(2024, 2, 1), date(2024, 2, 29)),
                 (date(2024, 3, 1), date(2024, 3, 31))]
    allocations = [allocate_leave_amount(start=date(2024, 1, 30), end=date(2024, 3, 2),
        period_start=begin, period_end=end, amount=Decimal(amount)) for begin, end in intervals]
    assert [days for days, _ in allocations] == [2, 29, 2]
    assert sum(value for _, value in allocations) == Decimal(amount)
    assert all(value >= 0 for _, value in allocations)


def test_no_overlap_and_single_day():
    kwargs = dict(start=date(2026, 9, 30), end=date(2026, 9, 30), amount=Decimal('1.23'))
    assert allocate_leave_amount(**kwargs, period_start=date(2026, 9, 1), period_end=date(2026, 9, 30)) == (1, Decimal('1.23'))
    assert allocate_leave_amount(**kwargs, period_start=date(2026, 10, 1), period_end=date(2026, 10, 31)) == (0, Decimal('0.00'))
