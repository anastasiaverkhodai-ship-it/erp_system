from copy import deepcopy

import pytest

from app.services.payroll_regulatory_2026_normative_controls import (
    NormativeControlError,
    applicable_version,
    build_normative_review,
    review_d6_applicability,
    review_gross_paid_candidate,
    sha256,
    verify_normative_review,
)


def test_july_2026_new_legal_entity_version():
    version = applicable_version(
        "2026-07",
        entity_type="legal_entity",
    )
    assert version["instrument"] == (
        "Ministry of Finance Order 243/2026"
    )


@pytest.mark.parametrize(
    "month", ["2026-06", "2025-12", "invalid"]
)
def test_unsupported_period_rejected(month):
    with pytest.raises(NormativeControlError):
        applicable_version(
            month, entity_type="legal_entity"
        )


def test_fop_not_silently_assigned_legal_entity_rules():
    with pytest.raises(NormativeControlError):
        applicable_version(
            "2026-09", entity_type="fop"
        )


def test_verified_payment_arithmetic_candidate():
    result = review_gross_paid_candidate(
        net_paid="770.00",
        pit_withheld_at_payment="180.00",
        levy_withheld_at_payment="50.00",
        candidate_gross_paid="1000.00",
        posted_payment_verified=True,
        withholding_allocation_verified=True,
    )
    assert result["arithmetic_reconciliation"] == "PASS"
    assert result["statutory_gross_paid_approved"] is False
    assert result["official_generation"] == "BLOCKED"


def test_wrong_gross_paid_candidate_rejected():
    with pytest.raises(NormativeControlError):
        review_gross_paid_candidate(
            net_paid="770.00",
            pit_withheld_at_payment="180.00",
            levy_withheld_at_payment="50.00",
            candidate_gross_paid="999.99",
            posted_payment_verified=True,
            withholding_allocation_verified=True,
        )


def test_unverified_withholding_rejected():
    with pytest.raises(NormativeControlError):
        review_gross_paid_candidate(
            net_paid="770.00",
            pit_withheld_at_payment="180.00",
            levy_withheld_at_payment="50.00",
            candidate_gross_paid="1000.00",
            posted_payment_verified=True,
            withholding_allocation_verified=False,
        )


def test_d6_absence_with_verified_no_positions():
    result = review_d6_applicability(
        special_service_positions=0,
        eligibility_evidence_verified=True,
    )
    assert result["d6_generation"] == "NOT_APPLICABLE"
    assert result["official_generation"] == "BLOCKED"


def test_d6_unknown_never_approves_absence():
    result = review_d6_applicability(
        special_service_positions=None,
        eligibility_evidence_verified=False,
    )
    assert result["status"] == "APPLICABILITY_UNKNOWN"
    assert result["d6_generation"] == "BLOCKED"


def test_d6_positive_count_needs_normative_review():
    result = review_d6_applicability(
        special_service_positions=1,
        eligibility_evidence_verified=True,
    )
    assert result["d6_generation"] == "BLOCKED"


def test_document_integrity_roundtrip():
    review = build_normative_review(
        company_id=1,
        payroll_period_id=2,
        month="2026-09",
    )
    assert verify_normative_review(review) is True
    assert review["body"]["official_generation"] == "BLOCKED"


def test_normative_review_tamper_rejected():
    review = build_normative_review(
        company_id=1,
        payroll_period_id=2,
        month="2026-09",
    )
    changed = deepcopy(review)
    changed["body"]["official_generation"] = "ENABLED"
    changed["review_sha256"] = sha256(
        changed["body"]
    )
    with pytest.raises(NormativeControlError):
        verify_normative_review(changed)


def test_document_digest_tamper_rejected():
    review = build_normative_review(
        company_id=1,
        payroll_period_id=2,
        month="2026-09",
    )
    review["review_sha256"] = "0" * 64
    with pytest.raises(
        NormativeControlError, match="fingerprint"
    ):
        verify_normative_review(review)
