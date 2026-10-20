from decimal import Decimal

import pytest

from app.schemas.employment_termination_basis import (
    EmploymentTerminationBasis,
)

from app.services.payroll_termination_severance_policy import (
    classify_severance_basis,
    calculate_statutory_severance_minimum,
)


@pytest.mark.parametrize(
    "ground,provision",
    [
        ("refusal_relocation_or_changed_terms", "36.6"),
        ("redundancy", "40.1"),
        ("incompatibility", "40.2"),
        ("reinstatement_of_previous_employee", "40.6"),
        ("other", "41.6"),
    ],
)
def test_one_month_minimum(ground, provision):
    policy = classify_severance_basis(
        EmploymentTerminationBasis(
            ground=ground,
            labour_code_provision=provision,
            source_reference="Verified termination order",
        )
    )

    assert policy.category == "average_months"
    assert policy.multiplier == 1

    assert calculate_statutory_severance_minimum(
        policy,
        average_monthly_earnings=Decimal("12000.00"),
    ) == Decimal("12000.00")


def test_military_service_minimum():
    policy = classify_severance_basis({
        "ground": "military_service",
        "labour_code_provision": "36.3",
        "source_reference": "Verified statutory documents",
    })

    assert policy.category == "minimum_wages"

    assert calculate_statutory_severance_minimum(
        policy,
        effective_minimum_wage=Decimal("8000.00"),
    ) == Decimal("16000.00")


def test_six_month_minimum():
    policy = classify_severance_basis({
        "ground": "other",
        "labour_code_provision": "41.5",
        "source_reference": "Verified Art. 41(5) documents",
    })

    assert calculate_statutory_severance_minimum(
        policy,
        average_monthly_earnings=Decimal("10000.00"),
    ) == Decimal("60000.00")


@pytest.mark.parametrize(
    "ground,provision",
    [
        ("education", "38"),
        ("retirement", "38"),
        ("other", "38"),
        ("other", "39"),
        ("other", "36.1"),
        ("long_incapacity", "40.5"),
        ("transfer_or_elected_position", "36.5"),
    ],
)
def test_no_unproven_automatic_entitlement(ground, provision):
    policy = classify_severance_basis({
        "ground": ground,
        "labour_code_provision": provision,
        "source_reference": "Reviewed evidence",
    })

    assert policy.category == "manual_review"

    with pytest.raises(ValueError, match="requires review"):
        calculate_statutory_severance_minimum(
            policy,
            average_monthly_earnings=Decimal("10000"),
        )


def test_missing_termination_evidence_is_not_entitlement():
    policy = classify_severance_basis(None)

    assert policy.category == "manual_review"


def test_undocumented_amount_cannot_be_calculated():
    policy = classify_severance_basis({
        "ground": "redundancy",
        "labour_code_provision": "40.1",
        "source_reference": "Verified termination order",
    })

    with pytest.raises(ValueError, match="basis is required"):
        calculate_statutory_severance_minimum(policy)

    with pytest.raises(ValueError, match="Decimal"):
        calculate_statutory_severance_minimum(
            policy,
            average_monthly_earnings=12000,
        )

    with pytest.raises(ValueError, match="Invalid"):
        calculate_statutory_severance_minimum(
            policy,
            average_monthly_earnings=Decimal("NaN"),
        )


def test_incorrect_legal_ground_is_rejected():
    with pytest.raises(
        ValueError,
        match="does not match",
    ):
        classify_severance_basis({
            "ground": "redundancy",
            "labour_code_provision": "36.1",
            "source_reference": "Mismatched order",
        })
