from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.services.sales_ar_aging_calculation_service import (
    HistoricalSettlement,
    SalesARAgingBucket,
    SalesARAgingCalculationError,
    calculate_historical_settled_amount,
    calculate_sales_ar_aging_projection,
)


def dt(
    year: int,
    month: int,
    day: int,
) -> datetime:
    return datetime(
        year,
        month,
        day,
        12,
        0,
        tzinfo=timezone.utc,
    )


def test_no_settlements_keeps_full_receivable_current():
    result = calculate_sales_ar_aging_projection(
        original_amount=Decimal("100.00"),
        due_date=date(2026, 9, 30),
        as_of_date=date(2026, 9, 15),
    )

    assert result.settled_amount == Decimal("0.00")
    assert result.open_amount == Decimal("100.00")
    assert result.days_overdue == 0
    assert result.bucket == SalesARAgingBucket.CURRENT


def test_settlement_created_before_as_of_reduces_balance():
    result = calculate_sales_ar_aging_projection(
        original_amount=Decimal("100.00"),
        due_date=date(2026, 9, 10),
        as_of_date=date(2026, 9, 15),
        settlements=(
            HistoricalSettlement(
                amount=Decimal("40.00"),
                created_at=dt(2026, 9, 5),
            ),
        ),
    )

    assert result.settled_amount == Decimal("40.00")
    assert result.open_amount == Decimal("60.00")
    assert result.days_overdue == 5
    assert result.bucket == SalesARAgingBucket.DAYS_1_30


def test_future_settlement_does_not_reduce_historical_balance():
    result = calculate_sales_ar_aging_projection(
        original_amount=Decimal("100.00"),
        due_date=date(2026, 8, 31),
        as_of_date=date(2026, 9, 15),
        settlements=(
            HistoricalSettlement(
                amount=Decimal("40.00"),
                created_at=dt(2026, 9, 20),
            ),
        ),
    )

    assert result.settled_amount == Decimal("0.00")
    assert result.open_amount == Decimal("100.00")


def test_reversal_after_as_of_keeps_settlement_historically_active():
    result = calculate_sales_ar_aging_projection(
        original_amount=Decimal("100.00"),
        due_date=date(2026, 8, 31),
        as_of_date=date(2026, 9, 15),
        settlements=(
            HistoricalSettlement(
                amount=Decimal("40.00"),
                created_at=dt(2026, 9, 1),
                reversed_at=dt(2026, 9, 20),
            ),
        ),
    )

    assert result.settled_amount == Decimal("40.00")
    assert result.open_amount == Decimal("60.00")


def test_reversal_on_as_of_date_removes_settlement():
    result = calculate_sales_ar_aging_projection(
        original_amount=Decimal("100.00"),
        due_date=date(2026, 8, 31),
        as_of_date=date(2026, 9, 15),
        settlements=(
            HistoricalSettlement(
                amount=Decimal("40.00"),
                created_at=dt(2026, 9, 1),
                reversed_at=dt(2026, 9, 15),
            ),
        ),
    )

    assert result.settled_amount == Decimal("0.00")
    assert result.open_amount == Decimal("100.00")


@pytest.mark.parametrize(
    ("due_date", "expected_days", "expected_bucket"),
    (
        (
            date(2026, 9, 15),
            0,
            SalesARAgingBucket.CURRENT,
        ),
        (
            date(2026, 9, 14),
            1,
            SalesARAgingBucket.DAYS_1_30,
        ),
        (
            date(2026, 8, 16),
            30,
            SalesARAgingBucket.DAYS_1_30,
        ),
        (
            date(2026, 8, 15),
            31,
            SalesARAgingBucket.DAYS_31_60,
        ),
        (
            date(2026, 7, 17),
            60,
            SalesARAgingBucket.DAYS_31_60,
        ),
        (
            date(2026, 7, 16),
            61,
            SalesARAgingBucket.DAYS_61_90,
        ),
        (
            date(2026, 6, 17),
            90,
            SalesARAgingBucket.DAYS_61_90,
        ),
        (
            date(2026, 6, 16),
            91,
            SalesARAgingBucket.DAYS_91_PLUS,
        ),
    ),
)
def test_aging_bucket_boundaries(
    due_date,
    expected_days,
    expected_bucket,
):
    result = calculate_sales_ar_aging_projection(
        original_amount=Decimal("100.00"),
        due_date=due_date,
        as_of_date=date(2026, 9, 15),
    )

    assert result.days_overdue == expected_days
    assert result.bucket == expected_bucket


def test_multiple_historical_settlements_are_summed():
    settled = calculate_historical_settled_amount(
        as_of_date=date(2026, 9, 15),
        settlements=(
            HistoricalSettlement(
                amount=Decimal("20.00"),
                created_at=dt(2026, 9, 1),
            ),
            HistoricalSettlement(
                amount=Decimal("30.00"),
                created_at=dt(2026, 9, 10),
            ),
        ),
    )

    assert settled == Decimal("50.00")


def test_over_settlement_fails_closed():
    with pytest.raises(
        SalesARAgingCalculationError,
        match="exceeds original",
    ):
        calculate_sales_ar_aging_projection(
            original_amount=Decimal("100.00"),
            due_date=date(2026, 9, 1),
            as_of_date=date(2026, 9, 15),
            settlements=(
                HistoricalSettlement(
                    amount=Decimal("101.00"),
                    created_at=dt(2026, 9, 1),
                ),
            ),
        )


def test_invalid_reversal_chronology_fails_closed():
    with pytest.raises(
        SalesARAgingCalculationError,
        match="cannot precede",
    ):
        calculate_historical_settled_amount(
            as_of_date=date(2026, 9, 15),
            settlements=(
                HistoricalSettlement(
                    amount=Decimal("10.00"),
                    created_at=dt(2026, 9, 10),
                    reversed_at=dt(2026, 9, 9),
                ),
            ),
        )
