"""Candidate legal classifications for Ukrainian payroll deductions.

This module does not authorize a deduction.
Document authenticity and applicable legal rules must be
verified separately before payroll integration.
"""

from dataclasses import dataclass
from decimal import Decimal


class PayrollDeductionClassificationError(ValueError):
    pass


@dataclass(frozen=True)
class DeductionLegalClassification:
    category: str
    individual_limit_percentage: Decimal
    aggregate_limit_percentage: Decimal
    legal_reference: str


CATEGORIES = {
    "employer_statutory": DeductionLegalClassification(
        category="employer_statutory",
        individual_limit_percentage=Decimal("20"),
        aggregate_limit_percentage=Decimal("20"),
        legal_reference="KZpP Articles 127-128",
    ),
    "enforcement_other": DeductionLegalClassification(
        category="enforcement_other",
        individual_limit_percentage=Decimal("20"),
        aggregate_limit_percentage=Decimal("50"),
        legal_reference="Law 1404-VIII Article 70",
    ),
    "enforcement_special": DeductionLegalClassification(
        category="enforcement_special",
        individual_limit_percentage=Decimal("50"),
        aggregate_limit_percentage=Decimal("50"),
        legal_reference="Law 1404-VIII Article 70",
    ),
    "minor_child_alimony": DeductionLegalClassification(
        category="minor_child_alimony",
        individual_limit_percentage=Decimal("70"),
        aggregate_limit_percentage=Decimal("70"),
        legal_reference="Law 1404-VIII Article 70",
    ),
}


def classify_deduction(
    *,
    category: str,
    legal_document_verified: bool,
    legal_document_reference: str | None,
) -> DeductionLegalClassification:
    """Return candidate limits for an explicitly classified deduction."""

    if category not in CATEGORIES:
        raise PayrollDeductionClassificationError(
            "Unknown legal deduction category"
        )

    if legal_document_verified is not True:
        raise PayrollDeductionClassificationError(
            "Verified legal document is required"
        )

    if (
        not isinstance(legal_document_reference, str)
        or not legal_document_reference.strip()
    ):
        raise PayrollDeductionClassificationError(
            "Legal document reference is required"
        )

    return CATEGORIES[category]
