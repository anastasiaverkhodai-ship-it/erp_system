"""Legal review contract for Ukrainian payroll deductions.

This module records review requirements. It does not establish
that candidate percentages are legally applicable.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.services.payroll_deduction_legal_classification import (
    CATEGORIES,
)


class PayrollDeductionLegalRuleError(ValueError):
    pass


@dataclass(frozen=True)
class LegalRuleReview:
    category: str
    candidate_individual_percentage: Decimal
    candidate_aggregate_percentage: Decimal
    legal_reference: str
    legally_approved: bool
    automatic_payroll_application_allowed: bool


def get_legal_rule_review(category: str) -> LegalRuleReview:
    """Return candidate rules with production activation disabled."""

    classification = CATEGORIES.get(category)

    if classification is None:
        raise PayrollDeductionLegalRuleError(
            "Unknown payroll deduction legal category"
        )

    return LegalRuleReview(
        category=classification.category,
        candidate_individual_percentage=(
            classification.individual_limit_percentage
        ),
        candidate_aggregate_percentage=(
            classification.aggregate_limit_percentage
        ),
        legal_reference=classification.legal_reference,
        legally_approved=False,
        automatic_payroll_application_allowed=False,
    )


def require_approved_legal_rule(category: str) -> LegalRuleReview:
    """Fail closed until a separate legal approval mechanism exists."""

    review = get_legal_rule_review(category)

    if not review.legally_approved:
        raise PayrollDeductionLegalRuleError(
            "Legal rule has not been approved for payroll"
        )

    if not review.automatic_payroll_application_allowed:
        raise PayrollDeductionLegalRuleError(
            "Automatic payroll application is disabled"
        )

    return review
