"""Documented minimum severance policy for termination.

This module classifies statutory minimums; it does not post
payroll, infer missing legal evidence or create financial records.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.schemas.employment_termination_basis import (
    EmploymentTerminationBasis,
)


@dataclass(frozen=True)
class SeverancePolicy:
    category: Literal[
        "average_months",
        "minimum_wages",
        "manual_review",
    ]
    multiplier: int | None
    labour_code_provision: str | None
    source_reference: str | None


AVERAGE_MONTH_GROUNDS = frozenset({
    "refusal_relocation_or_changed_terms",
    "redundancy",
    "incompatibility",
    "reinstatement_of_previous_employee",
})


def classify_severance_basis(
    basis: EmploymentTerminationBasis | dict | None,
) -> SeverancePolicy:
    """Classify a documented termination ground under Art. 44."""

    if basis is None:
        return SeverancePolicy(
            "manual_review", None, None, None
        )

    basis = EmploymentTerminationBasis.model_validate(basis)

    provision = basis.labour_code_provision
    reference = basis.source_reference

    if basis.ground in AVERAGE_MONTH_GROUNDS:
        return SeverancePolicy(
            "average_months", 1, provision, reference
        )

    if basis.ground == "military_service":
        return SeverancePolicy(
            "minimum_wages", 2, provision, reference
        )

    # Other statutory Art. 41 grounds are distinguishable
    # only by a specifically documented Labour Code provision.
    if basis.ground == "other" and provision == "41.5":
        return SeverancePolicy(
            "average_months", 6, provision, reference
        )

    if basis.ground == "other" and provision == "41.6":
        return SeverancePolicy(
            "average_months", 1, provision, reference
        )

    # Art. 38/39 does not, by itself, prove employer breach.
    # Education, retirement and other grounds must not be
    # interpreted as automatic severance eligibility.
    return SeverancePolicy(
        "manual_review", None, provision, reference
    )


def calculate_statutory_severance_minimum(
    policy: SeverancePolicy,
    *,
    average_monthly_earnings: Decimal | None = None,
    effective_minimum_wage: Decimal | None = None,
) -> Decimal:
    """Calculate a minimum using separately verified UAH bases.

    Amounts and their dated documentary provenance must be
    validated by the calling payroll service before posting.
    """

    if policy.category == "manual_review":
        raise ValueError(
            "Documented severance eligibility requires review"
        )

    if policy.category == "average_months":
        value = average_monthly_earnings
    elif policy.category == "minimum_wages":
        value = effective_minimum_wage
    else:
        raise ValueError("Unsupported severance category")

    if value is None:
        raise ValueError(
            "Verified statutory calculation basis is required"
        )

    if not isinstance(value, Decimal):
        raise ValueError(
            "Verified amount must be a Decimal"
        )

    if not value.is_finite() or value < 0:
        raise ValueError(
            "Invalid statutory calculation basis"
        )

    if policy.multiplier is None or policy.multiplier <= 0:
        raise ValueError(
            "Invalid statutory multiplier"
        )

    return (value * policy.multiplier).quantize(
        Decimal("0.01")
    )
