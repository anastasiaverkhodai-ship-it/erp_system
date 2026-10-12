"""Conservative payroll deduction limit calculation primitives.

This module does not classify legal grounds or authorize deductions.
Callers must supply a separately verified applicable limit.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


MONEY = Decimal("0.01")

SUPPORTED_LIMITS = frozenset({
    Decimal("20"),
    Decimal("50"),
    Decimal("70"),
})


class PayrollDeductionLimitError(ValueError):
    """Invalid or unsupported deduction limit calculation."""


def money(value: Decimal) -> Decimal:
    value = Decimal(value)

    if not value.is_finite():
        raise PayrollDeductionLimitError(
            "Amount must be finite"
        )

    return value.quantize(
        MONEY,
        rounding=ROUND_HALF_UP,
    )


@dataclass(frozen=True)
class DeductionLimitResult:
    statutory_net: Decimal
    requested_amount: Decimal
    already_withheld: Decimal
    maximum_total: Decimal
    available_limit: Decimal
    permitted_amount: Decimal
    unapplied_amount: Decimal
    limit_percentage: Decimal


def calculate_deduction_limit(
    *,
    statutory_net: Decimal,
    requested_amount: Decimal,
    already_withheld: Decimal,
    verified_limit_percentage: Decimal,
) -> DeductionLimitResult:
    """Calculate remaining capacity under a verified aggregate cap.

    A percentage supplied here is not proof that it is legally
    applicable. Classification and legal authorization are external.
    """

    net = money(statutory_net)
    requested = money(requested_amount)
    withheld = money(already_withheld)

    percentage = Decimal(verified_limit_percentage)

    if not percentage.is_finite():
        raise PayrollDeductionLimitError(
            "Limit percentage must be finite"
        )

    if percentage not in SUPPORTED_LIMITS:
        raise PayrollDeductionLimitError(
            "Unsupported deduction limit percentage"
        )

    if net < 0:
        raise PayrollDeductionLimitError(
            "Statutory net cannot be negative"
        )

    if requested < 0:
        raise PayrollDeductionLimitError(
            "Requested deduction cannot be negative"
        )

    if withheld < 0:
        raise PayrollDeductionLimitError(
            "Already withheld amount cannot be negative"
        )

    maximum = money(
        net * percentage / Decimal("100")
    )

    if withheld > maximum:
        raise PayrollDeductionLimitError(
            "Existing deductions exceed verified limit"
        )

    available = money(maximum - withheld)
    permitted = min(requested, available)

    return DeductionLimitResult(
        statutory_net=net,
        requested_amount=requested,
        already_withheld=withheld,
        maximum_total=maximum,
        available_limit=available,
        permitted_amount=permitted,
        unapplied_amount=money(requested - permitted),
        limit_percentage=percentage,
    )
