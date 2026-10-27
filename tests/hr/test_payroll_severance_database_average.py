from datetime import date
from decimal import Decimal
from types import SimpleNamespace as N

import pytest

from app.services.payroll_severance_average_earnings import (
    SeveranceAverageError,
)
from app.services.payroll_severance_database_average import (
    _validated_month,
    derive_database_severance_average,
)


def sample(**changes):
    data = dict(
        period=N(
            id=1,
            status="finalized",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 31),
        ),
        calculation=N(
            id=2,
            status="calculated",
            currency_code="UAH",
            payroll_input_id=3,
            payroll_period_id=1,
            gross_amount=Decimal("1000.00"),
        ),
        payroll_input=N(
            id=3, worked_minutes=960,
        ),
        lines=[
            N(
                line_type="salary",
                currency_code="UAH",
                amount=Decimal("1000.00"),
            )
        ],
        attendance=[
            N(
                work_date=date(2026, 7, 1),
                status="present",
                worked_minutes=480,
            ),
            N(
                work_date=date(2026, 7, 2),
                status="present",
                worked_minutes=480,
            ),
        ],
        schedule_minutes={
            date(2026, 7, 1): 480,
            date(2026, 7, 2): 480,
            date(2026, 7, 3): 0,
        },
        month=date(2026, 7, 1),
        month_end=date(2026, 7, 31),
    )
    data.update(changes)
    return data


def test_salary_only_reconciles():
    assert _validated_month(**sample()) == (
        Decimal("1000.00"),
        Decimal("2"),
        Decimal("2"),
    )


@pytest.mark.parametrize("line_type", [
    "minimum_wage",
    "supplement",
    "vacation_pay",
    "sick_pay",
    "severance",
    "manual_adjustment",
    "leave_compensation",
])
def test_unclassified_income_rejected(line_type):
    with pytest.raises(SeveranceAverageError):
        _validated_month(**sample(
            lines=[N(
                line_type=line_type,
                currency_code="UAH",
                amount=Decimal("1000.00"),
            )]
        ))


def test_unfinalized_period_rejected():
    args = sample()
    args["period"].status = "draft"
    with pytest.raises(SeveranceAverageError):
        _validated_month(**args)


def test_gross_mismatch_rejected():
    args = sample()
    args["calculation"].gross_amount = Decimal("2000")
    with pytest.raises(SeveranceAverageError):
        _validated_month(**args)


def test_missing_attendance_rejected():
    with pytest.raises(SeveranceAverageError):
        _validated_month(**sample(attendance=[]))


def test_schedule_gap_rejected():
    with pytest.raises(SeveranceAverageError):
        _validated_month(**sample(
            schedule_minutes={date(2026, 7, 1): 480}
        ))


def test_duplicate_attendance_rejected():
    args = sample()
    args["attendance"].append(args["attendance"][0])
    with pytest.raises(SeveranceAverageError):
        _validated_month(**args)


def test_absent_day_rejected():
    args = sample()
    args["attendance"][0].status = "absent"
    with pytest.raises(SeveranceAverageError):
        _validated_month(**args)


@pytest.mark.asyncio
async def test_invalid_company_rejected_before_db_call():
    class NoDatabase:
        def __getattr__(self, name):
            raise AssertionError("Unexpected database access")

    with pytest.raises(SeveranceAverageError):
        await derive_database_severance_average(
            NoDatabase(),
            company_id=0,
            employment_contract_id=1,
            termination_date=date(2026, 9, 30),
        )
