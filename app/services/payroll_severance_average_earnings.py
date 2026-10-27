"""Order 100 severance average: validated arithmetic kernel.

This kernel does not certify source earnings or authorize payments.
The caller must supply verified eligible income, actual worked days
and scheduled workdays for the legally selected reference period.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP


CENT = Decimal("0.01")


class SeveranceAverageError(ValueError):
    """Insufficient or inconsistent average-earnings evidence."""


@dataclass(frozen=True)
class SeveranceAverageResult:
    reference_start: date
    reference_end: date
    eligible_earnings: Decimal
    worked_days: Decimal
    scheduled_days: Decimal
    average_daily: Decimal
    average_monthly: Decimal
    rule_code: str
    source_reference: str


def _decimal(value, label, *, positive=False):
    if isinstance(value, (float, bool)):
        raise SeveranceAverageError(
            f"{label} requires an exact decimal value"
        )
    try:
        result = Decimal(value)
    except (TypeError, ValueError, ArithmeticError):
        raise SeveranceAverageError(
            f"{label} is invalid"
        ) from None

    if not result.is_finite():
        raise SeveranceAverageError(
            f"{label} must be finite"
        )
    if result < 0 or (positive and result == 0):
        raise SeveranceAverageError(
            f"{label} must be positive"
        )
    return result


def calculate_severance_average(
    *,
    reference_start: date,
    reference_end: date,
    eligible_earnings: Decimal,
    worked_days: Decimal,
    scheduled_days: Decimal,
    reference_months: int,
    source_reference: str,
) -> SeveranceAverageResult:
    """Calculate a reviewed reference-period average.

    Does not automatically select the period or determine whether
    earnings are legally eligible. Those checks are mandatory before
    integrating this result with immutable severance sources.
    """
    if (
        not isinstance(reference_start, date)
        or not isinstance(reference_end, date)
        or reference_start > reference_end
    ):
        raise SeveranceAverageError(
            "Invalid reference period"
        )

    if reference_months != 2 or type(reference_months) is not int:
        raise SeveranceAverageError(
            "Only verified two-month periods are supported"
        )

    if (
        not isinstance(source_reference, str)
        or not source_reference.strip()
    ):
        raise SeveranceAverageError(
            "Documented source reference required"
        )

    earnings = _decimal(
        eligible_earnings, "Eligible earnings", positive=True
    )
    worked = _decimal(
        worked_days, "Worked days", positive=True
    )
    scheduled = _decimal(
        scheduled_days, "Scheduled days", positive=True
    )

    if worked > scheduled:
        raise SeveranceAverageError(
            "Worked days exceed scheduled days"
        )

    if earnings.as_tuple().exponent < -2:
        raise SeveranceAverageError(
            "Earnings contain fractional kopecks"
        )

    daily = earnings / worked
    monthly = (
        daily * (scheduled / Decimal(reference_months))
    ).quantize(CENT, rounding=ROUND_HALF_UP)

    if monthly <= 0:
        raise SeveranceAverageError(
            "Calculated monthly average is invalid"
        )

    return SeveranceAverageResult(
        reference_start=reference_start,
        reference_end=reference_end,
        eligible_earnings=earnings,
        worked_days=worked,
        scheduled_days=scheduled,
        average_daily=daily,
        average_monthly=monthly,
        rule_code="UA_ORDER_100_SEVERANCE_2_MONTH",
        source_reference=source_reference.strip(),
    )
