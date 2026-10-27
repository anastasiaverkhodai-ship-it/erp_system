"""Structural validation for dated statutory income allocations.

This module does not verify database provenance or legislative authority.
Neither this validator nor its result authorizes payroll persistence.
Database source/rate verification must occur before the atomic writer.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal


class DatedIncomeAllocationError(ValueError):
    pass


@dataclass(frozen=True)
class DatedIncomeSlice:
    source_id: int
    contract_id: int
    income_from: date
    income_to: date
    gross_amount: Decimal
    rate_id: int
    rate_from: date
    rate_to: date
    rate: Decimal


def validate_dated_income_slices(
    slices,
    *,
    contract_id,
    period_start,
    period_end,
    expected_gross,
):
    """Check structure and arithmetic, without trusting source IDs.

    Income intervals must cover the requested allocation period,
    without overlap or gaps. A separate DB preflight must verify
    source authenticity, earned-date attribution, rate authority,
    employee-wide constraints and statutory applicability.
    """
    money = Decimal("0.01")

    if (
        not isinstance(contract_id, int)
        or isinstance(contract_id, bool)
        or contract_id <= 0
        or not isinstance(period_start, date)
        or not isinstance(period_end, date)
        or period_start > period_end
    ):
        raise DatedIncomeAllocationError(
            "Invalid employee contract or allocation period"
        )

    try:
        gross = Decimal(expected_gross)
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise DatedIncomeAllocationError(
            "Invalid expected payroll gross"
        ) from exc

    if (
        not gross.is_finite()
        or gross < 0
        or gross != gross.quantize(money)
    ):
        raise DatedIncomeAllocationError(
            "Expected payroll gross must be nonnegative UAH cents"
        )

    rows = tuple(slices)

    if not rows:
        raise DatedIncomeAllocationError(
            "Dated income allocation is empty"
        )

    if len(rows) > 366:
        raise DatedIncomeAllocationError(
            "Too many dated income slices"
        )

    ids = set()
    normalized = []

    for row in rows:
        if not isinstance(row, DatedIncomeSlice):
            raise DatedIncomeAllocationError(
                "Unsupported dated income slice"
            )

        if (
            not isinstance(row.source_id, int)
            or isinstance(row.source_id, bool)
            or row.source_id <= 0
            or not isinstance(row.rate_id, int)
            or isinstance(row.rate_id, bool)
            or row.rate_id <= 0
            or row.contract_id != contract_id
        ):
            raise DatedIncomeAllocationError(
                "Invalid dated income source or rate identity"
            )

        if row.source_id in ids:
            raise DatedIncomeAllocationError(
                "Duplicate dated income source"
            )

        ids.add(row.source_id)

        dates = (
            row.income_from,
            row.income_to,
            row.rate_from,
            row.rate_to,
        )
        if not all(isinstance(value, date) for value in dates):
            raise DatedIncomeAllocationError(
                "Missing dated income or rate interval"
            )

        if not (
            period_start <= row.income_from <= row.income_to <= period_end
            and row.rate_from <= row.income_from
            and row.income_to <= row.rate_to
        ):
            raise DatedIncomeAllocationError(
                "Income falls outside payroll period or statutory rate"
            )

        try:
            amount = Decimal(row.gross_amount)
            rate = Decimal(row.rate)
        except (TypeError, ValueError, ArithmeticError) as exc:
            raise DatedIncomeAllocationError(
                "Invalid dated income amount or rate"
            ) from exc

        if (
            not amount.is_finite()
            or amount < 0
            or amount != amount.quantize(money)
            or not rate.is_finite()
            or not Decimal("0") <= rate <= Decimal("1")
        ):
            raise DatedIncomeAllocationError(
                "Invalid dated income cents or statutory rate"
            )

        normalized.append(row)

    ordered = sorted(
        normalized,
        key=lambda row: (row.income_from, row.income_to, row.source_id),
    )

    cursor = period_start

    for row in ordered:
        if row.income_from != cursor:
            raise DatedIncomeAllocationError(
                "Overlapping or discontinuous dated income intervals"
            )

        cursor = row.income_to + timedelta(days=1)

    if cursor != period_end + timedelta(days=1):
        raise DatedIncomeAllocationError(
            "Dated income does not cover the full allocation period"
        )

    total = sum(
        (Decimal(row.gross_amount) for row in ordered),
        Decimal("0.00"),
    )

    if total != gross:
        raise DatedIncomeAllocationError(
            "Dated income does not reconcile to payroll gross"
        )

    # Return immutable, ordered rows. This is a validated shape,
    # NOT a trusted or authorized payroll calculation.
    return tuple(ordered)
