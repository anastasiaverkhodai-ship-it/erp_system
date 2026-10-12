"""Candidate individual and aggregate payroll deduction limits.

This module does not authorize deductions or establish legal
applicability. It performs calculations only.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.services.payroll_deduction_legal_classification import (
    DeductionLegalClassification,
)
from app.services.payroll_deduction_limit_policy import (
    calculate_deduction_limit,
    money,
)


class PayrollVerifiedLimitError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedDeductionLimit:
    category: str
    requested_amount: Decimal
    permitted_amount: Decimal
    unapplied_amount: Decimal
    individual_maximum: Decimal
    aggregate_maximum: Decimal
    aggregate_remaining: Decimal


def calculate_verified_deduction_limit(
    *,
    statutory_net: Decimal,
    requested_amount: Decimal,
    already_withheld: Decimal,
    classification: DeductionLegalClassification,
) -> VerifiedDeductionLimit:

    if not isinstance(
        classification,
        DeductionLegalClassification,
    ):
        raise PayrollVerifiedLimitError(
            "Verified classification required"
        )

    try:
        net = money(statutory_net)
        requested = money(requested_amount)
        withheld = money(already_withheld)

        if min(net, requested, withheld) < 0:
            raise PayrollVerifiedLimitError(
                "Negative amounts are not allowed"
            )

        individual = calculate_deduction_limit(
            statutory_net=net,
            requested_amount=requested,
            already_withheld=Decimal("0.00"),
            verified_limit_percentage=(
                classification.individual_limit_percentage
            ),
        )

        aggregate = calculate_deduction_limit(
            statutory_net=net,
            requested_amount=requested,
            already_withheld=withheld,
            verified_limit_percentage=(
                classification.aggregate_limit_percentage
            ),
        )

        permitted = min(
            individual.permitted_amount,
            aggregate.permitted_amount,
        )

        return VerifiedDeductionLimit(
            category=classification.category,
            requested_amount=requested,
            permitted_amount=permitted,
            unapplied_amount=money(requested - permitted),
            individual_maximum=individual.maximum_total,
            aggregate_maximum=aggregate.maximum_total,
            aggregate_remaining=aggregate.available_limit,
        )

    except PayrollVerifiedLimitError:
        raise
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise PayrollVerifiedLimitError(
            "Invalid deduction limit calculation"
        ) from exc
