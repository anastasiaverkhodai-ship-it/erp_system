"""Structural classification of payroll income.

This module separates documented severance from other payroll income.

It DOES NOT establish tax treatment, authorize financial operations,
or replace the existing severance financial classification guard.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


MONEY = Decimal("0.01")
ZERO = Decimal("0.00")


class SeveranceIncomeClassificationError(ValueError):
    """The payroll income source breakdown is inconsistent."""


@dataclass(frozen=True)
class PayrollIncomeSplit:
    ordinary_gross: Decimal
    severance_gross: Decimal
    total_gross: Decimal
    severance_source_ids: tuple[int, ...]


def _money(value, *, label: str, allow_negative: bool = False) -> Decimal:
    """Accept exact monetary values without silent rounding."""

    if isinstance(value, (float, bool)):
        raise SeveranceIncomeClassificationError(
            f"{label} must use an exact decimal value"
        )

    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise SeveranceIncomeClassificationError(
            f"{label} is not a valid decimal amount"
        ) from None

    if not amount.is_finite():
        raise SeveranceIncomeClassificationError(
            f"{label} must be finite"
        )

    if amount < ZERO and not allow_negative:
        raise SeveranceIncomeClassificationError(
            f"{label} cannot be negative"
        )

    if amount.as_tuple().exponent < -2:
        raise SeveranceIncomeClassificationError(
            f"{label} cannot contain fractional kopecks"
        )

    return amount.quantize(MONEY)


def split_payroll_income(
    *,
    gross_amount,
    currency_code,
    lines,
) -> PayrollIncomeSplit:
    """Split payroll gross into ordinary and documented severance.

    Fail closed on inconsistent totals, currencies or source references.
    This is source classification only, NOT statutory classification.
    """

    if currency_code != "UAH":
        raise SeveranceIncomeClassificationError(
            "Income classification requires UAH"
        )

    expected_gross = _money(
        gross_amount,
        label="Payroll gross",
    )

    ordinary = ZERO
    severance = ZERO
    source_ids = []

    try:
        iterator = iter(lines)
    except TypeError:
        raise SeveranceIncomeClassificationError(
            "Payroll lines must be iterable"
        ) from None

    for index, line in enumerate(iterator, start=1):
        kind = getattr(line, "line_type", None)

        if not isinstance(kind, str) or not kind:
            raise SeveranceIncomeClassificationError(
                f"Line {index} has no valid income type"
            )

        line_currency = getattr(line, "currency_code", None)

        if line_currency != currency_code:
            raise SeveranceIncomeClassificationError(
                f"Line {index} currency does not match payroll"
            )

        amount = _money(
            getattr(line, "amount", None),
            label=f"Line {index} amount",
            allow_negative=(kind == "manual_adjustment"),
        )

        source = getattr(
            line,
            "source_termination_severance_id",
            None,
        )

        if kind == "severance":
            if (
                not isinstance(source, int)
                or isinstance(source, bool)
                or source <= 0
            ):
                raise SeveranceIncomeClassificationError(
                    f"Line {index} requires documented severance source"
                )

            if source in source_ids:
                raise SeveranceIncomeClassificationError(
                    "Duplicate severance source in payroll calculation"
                )

            severance += amount
            source_ids.append(source)

        else:
            if source is not None:
                raise SeveranceIncomeClassificationError(
                    f"Line {index} has severance source without severance type"
                )

            ordinary += amount

    actual_gross = ordinary + severance

    if actual_gross != expected_gross:
        raise SeveranceIncomeClassificationError(
            "Payroll gross does not equal classified income lines"
        )

    return PayrollIncomeSplit(
        ordinary_gross=ordinary,
        severance_gross=severance,
        total_gross=actual_gross,
        severance_source_ids=tuple(source_ids),
    )
