from decimal import Decimal

import pytest

from app.services.payroll_deduction_legal_classification import (
    PayrollDeductionClassificationError,
    classify_deduction,
)


@pytest.mark.parametrize(
    ("category", "individual", "aggregate"),
    [
        ("employer_statutory", "20", "20"),
        ("enforcement_other", "20", "50"),
        ("enforcement_special", "50", "50"),
        ("minor_child_alimony", "70", "70"),
    ],
)
def test_classification(category, individual, aggregate):
    result = classify_deduction(
        category=category,
        legal_document_verified=True,
        legal_document_reference="Verified document 123",
    )

    assert result.individual_limit_percentage == Decimal(individual)
    assert result.aggregate_limit_percentage == Decimal(aggregate)


def test_unknown_category_rejected():
    with pytest.raises(
        PayrollDeductionClassificationError,
        match="Unknown",
    ):
        classify_deduction(
            category="unknown",
            legal_document_verified=True,
            legal_document_reference="Document 123",
        )


def test_unverified_document_rejected():
    with pytest.raises(
        PayrollDeductionClassificationError,
        match="Verified",
    ):
        classify_deduction(
            category="enforcement_special",
            legal_document_verified=False,
            legal_document_reference="Document 123",
        )


def test_missing_reference_rejected():
    with pytest.raises(
        PayrollDeductionClassificationError,
        match="reference",
    ):
        classify_deduction(
            category="minor_child_alimony",
            legal_document_verified=True,
            legal_document_reference="",
        )
