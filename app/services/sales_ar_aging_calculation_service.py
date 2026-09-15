from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Iterable


ZERO = Decimal("0.00")
CENT = Decimal("0.01")


class SalesARAgingCalculationError(ValueError):
    pass


class SalesARAgingBucket(str, Enum):
    CURRENT = "current"
    DAYS_1_30 = "1_30"
    DAYS_31_60 = "31_60"
    DAYS_61_90 = "61_90"
    DAYS_91_PLUS = "91_plus"


@dataclass(frozen=True)
class HistoricalSettlement:
    amount: Decimal
    created_at: datetime
    reversed_at: datetime | None = None


@dataclass(frozen=True)
class SalesARAgingProjection:
    original_amount: Decimal
    settled_amount: Decimal
    open_amount: Decimal
    due_date: date
    as_of_date: date
    days_overdue: int
    bucket: SalesARAgingBucket


def _money(value: Decimal) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(
            CENT,
            rounding=ROUND_HALF_UP,
        )
    except Exception as exc:
        raise SalesARAgingCalculationError(
            "Monetary amount is invalid"
        ) from exc

    return amount


def _validate_datetime(
    value: datetime,
    *,
    label: str,
) -> datetime:
    if not isinstance(value, datetime):
        raise SalesARAgingCalculationError(
            f"{label} must be a datetime"
        )
    return value


def calculate_historical_settled_amount(
    *,
    settlements: Iterable[HistoricalSettlement],
    as_of_date: date,
) -> Decimal:
    if not isinstance(as_of_date, date):
        raise SalesARAgingCalculationError(
            "as_of_date must be a date"
        )

    total = ZERO

    for settlement in settlements:
        amount = _money(settlement.amount)

        if amount <= ZERO:
            raise SalesARAgingCalculationError(
                "Settlement amount must be greater than zero"
            )

        created_at = _validate_datetime(
            settlement.created_at,
            label="Settlement created_at",
        )

        reversed_at = settlement.reversed_at

        if reversed_at is not None:
            reversed_at = _validate_datetime(
                reversed_at,
                label="Settlement reversed_at",
            )

            if reversed_at < created_at:
                raise SalesARAgingCalculationError(
                    "Settlement reversed_at cannot precede created_at"
                )

        # Historical commercial activity:
        #
        # created on/before as-of date, and not reversed
        # until a later date.
        #
        # A reversal on the as-of date removes the allocation
        # from the closing balance for that date.
        if created_at.date() > as_of_date:
            continue

        if (
            reversed_at is not None
            and reversed_at.date() <= as_of_date
        ):
            continue

        total = _money(total + amount)

    return total


def classify_sales_ar_aging(
    *,
    due_date: date,
    as_of_date: date,
) -> tuple[int, SalesARAgingBucket]:
    if not isinstance(due_date, date):
        raise SalesARAgingCalculationError(
            "due_date must be a date"
        )

    if not isinstance(as_of_date, date):
        raise SalesARAgingCalculationError(
            "as_of_date must be a date"
        )

    days_overdue = max(
        (as_of_date - due_date).days,
        0,
    )

    if days_overdue == 0:
        return (
            0,
            SalesARAgingBucket.CURRENT,
        )

    if days_overdue <= 30:
        bucket = SalesARAgingBucket.DAYS_1_30
    elif days_overdue <= 60:
        bucket = SalesARAgingBucket.DAYS_31_60
    elif days_overdue <= 90:
        bucket = SalesARAgingBucket.DAYS_61_90
    else:
        bucket = SalesARAgingBucket.DAYS_91_PLUS

    return (
        days_overdue,
        bucket,
    )


def calculate_sales_ar_aging_projection(
    *,
    original_amount: Decimal,
    due_date: date,
    as_of_date: date,
    settlements: Iterable[HistoricalSettlement] = (),
) -> SalesARAgingProjection:
    original = _money(original_amount)

    if original <= ZERO:
        raise SalesARAgingCalculationError(
            "Open Item original amount must be greater than zero"
        )

    settled = calculate_historical_settled_amount(
        settlements=settlements,
        as_of_date=as_of_date,
    )

    if settled > original:
        raise SalesARAgingCalculationError(
            "Historical settled amount exceeds original amount"
        )

    open_amount = _money(
        original - settled
    )

    days_overdue, bucket = classify_sales_ar_aging(
        due_date=due_date,
        as_of_date=as_of_date,
    )

    return SalesARAgingProjection(
        original_amount=original,
        settled_amount=settled,
        open_amount=open_amount,
        due_date=due_date,
        as_of_date=as_of_date,
        days_overdue=days_overdue,
        bucket=bucket,
    )
