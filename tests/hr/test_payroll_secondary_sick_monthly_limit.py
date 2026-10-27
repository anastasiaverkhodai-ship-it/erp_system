
"""Shared calendar-month sick allocation invariants."""
from datetime import date
from decimal import Decimal as D

import pytest

from app.services.payroll_sick_allocation import (
    allocate_sick_days,
    maximum_contribution_base,
)


def test_prior_other_contract_consumes_same_month_ceiling():
    maximum = maximum_contribution_base(date(2026, 10, 1))
    previously_paid = maximum - D("100.00")

    rows = allocate_sick_days(
        start=date(2026, 10, 10),
        end=date(2026, 10, 12),
        case_start=date(2026, 10, 10),
        daily=D("80.00"),
        maximum=maximum,
        previous={"2026-10": previously_paid},
    )

    total = sum((D(row["amount"]) for row in rows), D(0))
    assert total == D("100.00")
    assert previously_paid + total == maximum
    assert [D(row["amount"]) for row in rows] == [
        D("80.00"), D("20.00"), D("0.00")
    ]


def test_ceiling_resets_only_at_calendar_month_boundary():
    rows = allocate_sick_days(
        start=date(2026, 10, 31),
        end=date(2026, 11, 2),
        case_start=date(2026, 10, 1),
        daily=D("80.00"),
        maximum=D("100.00"),
        previous={"2026-10": D("90.00")},
    )

    assert [D(row["amount"]) for row in rows] == [
        D("10.00"), D("80.00"), D("20.00")
    ]


@pytest.mark.parametrize("spent", [
    D("-1"), D("NaN"), D("Infinity")
])
def test_invalid_previous_consumption_is_rejected(spent):
    with pytest.raises(ValueError):
        allocate_sick_days(
            start=date(2026, 10, 5),
            end=date(2026, 10, 6),
            case_start=date(2026, 10, 5),
            daily=D("50.00"),
            maximum=D("100.00"),
            previous={"2026-10": spent},
        )


def test_no_double_allocation_when_month_fully_consumed():
    rows = allocate_sick_days(
        start=date(2026, 10, 20),
        end=date(2026, 10, 25),
        case_start=date(2026, 10, 1),
        daily=D("500.00"),
        maximum=D("1000.00"),
        previous={"2026-10": D("1000.00")},
    )
    assert all(D(row["amount"]) == 0 for row in rows)
