"""Validated monthly evidence for ordinary severance averages.

Does not read or write payroll tables. A separate ERP database
adapter must verify finalized source calculations, qualifying pay,
attendance, scheduled days and company ownership before constructing
these records. Imported vacation history is not severance evidence.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from calendar import monthrange
from decimal import Decimal

from app.services.payroll_severance_average_earnings import (
    SeveranceAverageError,
    calculate_severance_average,
)


@dataclass(frozen=True)
class SeveranceMonthlyEvidence:
    month: date
    eligible_earnings: Decimal
    worked_days: Decimal
    scheduled_days: Decimal
    source_type: str
    source_id: int
    source_reference: str
    company_id: int
    employment_contract_id: int
    verified_finalized: bool


ALLOWED_SOURCE_TYPES = frozenset({
    "verified_payroll_severance_basis",
})


def preceding_two_months(termination_date: date):
    if type(termination_date) is not date:
        raise SeveranceAverageError("Termination date required")

    current_month = termination_date.replace(day=1)
    previous_end = current_month - timedelta(days=1)
    previous_start = previous_end.replace(day=1)

    earlier_end = previous_start - timedelta(days=1)
    earlier_start = earlier_end.replace(day=1)

    return earlier_start, previous_start, previous_end


def derive_average_from_monthly_evidence(
    *,
    company_id: int,
    employment_contract_id: int,
    termination_date: date,
    evidence,
):
    """Reconcile two verified complete monthly evidence records.

    Special Order 100 reference-period cases and salary substitution
    must use an independently reviewed path, not this routine.
    """
    if type(company_id) is not int or company_id <= 0:
        raise SeveranceAverageError("Invalid company")
    if (
        type(employment_contract_id) is not int
        or employment_contract_id <= 0
    ):
        raise SeveranceAverageError("Invalid employment contract")

    start, second_month, end = preceding_two_months(
        termination_date
    )

    if not isinstance(evidence, (list, tuple)):
        raise SeveranceAverageError(
            "Monthly evidence must be a collection"
        )
    if len(evidence) != 2:
        raise SeveranceAverageError(
            "Exactly two verified monthly sources required"
        )

    months = (start, second_month)
    seen_ids = set()
    by_month = {}

    for row in evidence:
        if not isinstance(row, SeveranceMonthlyEvidence):
            raise SeveranceAverageError(
                "Invalid monthly source record"
            )

        if (
            row.company_id != company_id
            or row.employment_contract_id
            != employment_contract_id
        ):
            raise SeveranceAverageError(
                "Monthly evidence crosses company or contract"
            )

        if row.month not in months:
            raise SeveranceAverageError(
                "Monthly evidence outside reference period"
            )

        if row.month in by_month:
            raise SeveranceAverageError(
                "Duplicate reference month"
            )

        if row.source_type not in ALLOWED_SOURCE_TYPES:
            raise SeveranceAverageError(
                "Unapproved average earnings source type"
            )

        if (
            type(row.source_id) is not int
            or row.source_id <= 0
        ):
            raise SeveranceAverageError(
                "Invalid earnings source identifier"
            )

        key = (row.source_type, row.source_id)
        if key in seen_ids:
            raise SeveranceAverageError(
                "Duplicate earnings source identifier"
            )
        seen_ids.add(key)

        if row.verified_finalized is not True:
            raise SeveranceAverageError(
                "Verified finalized payroll source required"
            )

        if (
            not isinstance(row.source_reference, str)
            or not row.source_reference.strip()
        ):
            raise SeveranceAverageError(
                "Source documentary reference required"
            )

        # Validate each monthly source independently. A negative,
        # nonfinite or excessive value must not be concealed by the
        # other month's valid evidence.
        monthly_values = (
            ("eligible_earnings", row.eligible_earnings),
            ("worked_days", row.worked_days),
            ("scheduled_days", row.scheduled_days),
        )

        for label, value in monthly_values:
            if not isinstance(value, Decimal):
                raise SeveranceAverageError(
                    f"Monthly {label} requires Decimal evidence"
                )
            if not value.is_finite():
                raise SeveranceAverageError(
                    f"Monthly {label} must be finite"
                )
            if value < 0:
                raise SeveranceAverageError(
                    f"Monthly {label} cannot be negative"
                )

        if row.eligible_earnings.as_tuple().exponent < -2:
            raise SeveranceAverageError(
                "Monthly earnings contain fractional kopecks"
            )

        if row.worked_days > row.scheduled_days:
            raise SeveranceAverageError(
                "Monthly worked days exceed scheduled days"
            )

        if row.scheduled_days > Decimal("31"):
            raise SeveranceAverageError(
                "Monthly scheduled days exceed calendar limit"
            )

        if (
            row.eligible_earnings > 0
            and row.worked_days == 0
        ):
            raise SeveranceAverageError(
                "Positive monthly earnings require worked days"
            )

        by_month[row.month] = row

    if set(by_month) != set(months):
        raise SeveranceAverageError(
            "Missing reference month"
        )

    ordered = [by_month[month] for month in months]
    earnings = sum(
        (row.eligible_earnings for row in ordered),
        Decimal("0")
    )
    worked = sum(
        (row.worked_days for row in ordered),
        Decimal("0")
    )
    scheduled = sum(
        (row.scheduled_days for row in ordered),
        Decimal("0")
    )

    result = calculate_severance_average(
        reference_start=start,
        reference_end=end,
        eligible_earnings=earnings,
        worked_days=worked,
        scheduled_days=scheduled,
        reference_months=2,
        source_reference="; ".join(
            f"{row.month.isoformat()}:"
            f"{row.source_type}:{row.source_id}:"
            f"{row.source_reference.strip()}"
            for row in ordered
        ),
    )

    return result
