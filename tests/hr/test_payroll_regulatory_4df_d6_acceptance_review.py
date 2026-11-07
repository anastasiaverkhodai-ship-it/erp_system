from copy import deepcopy

import pytest

from app.services.payroll_regulatory_combined_financial_review import (
    canonical_digest,
)
from app.services.payroll_regulatory_4df_d6_acceptance_review import (
    FourDFD6AcceptanceError,
    positive,
    verify_review,
)


def sample():
    body = {
        "schema": "4DF_D6_ACCEPTANCE_REVIEW_V1",
        "company_id": 1,
        "payroll_period_id": 2,
        "combined_review_sha256": "a" * 64,
        "d1_source_integrity": "PASS",
        "four_df_accrual_status": "NOT_VERIFIED",
        "four_df_gross_paid_status":
            "NOT_NORMATIVELY_VERIFIED",
        "four_df_posted_payment_status": "NOT_SUPPLIED",
        "d6_source_status": "SOURCE_REVIEW_ONLY",
        "d6_normative_status": "NOT_APPROVED",
        "d6_evidence_sha256": "b" * 64,
        "d6_evidence_count": 1,
        "review_only": True,
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }
    return {
        **body,
        "review_sha256": canonical_digest(body),
    }


def test_valid_review_only_document():
    assert verify_review(sample()) is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("official_generation", "ENABLED"),
        ("four_df_gross_paid_status", "VERIFIED"),
        ("d6_normative_status", "APPROVED"),
        ("official_rows_generated", 1),
    ],
)
def test_unapproved_regulatory_claims_blocked(field, value):
    report = sample()
    report[field] = value
    report["review_sha256"] = canonical_digest({
        k: v for k, v in report.items()
        if k != "review_sha256"
    })

    with pytest.raises(FourDFD6AcceptanceError):
        verify_review(report)


def test_financial_review_tampering_blocked():
    report = sample()
    report["d6_evidence_count"] = 99
    with pytest.raises(
        FourDFD6AcceptanceError,
        match="fingerprint",
    ):
        verify_review(report)


def test_review_not_mutated():
    report = sample()
    before = deepcopy(report)
    verify_review(report)
    assert report == before


@pytest.mark.parametrize("value", [0, -1, True, "1", None])
def test_invalid_identity(value):
    with pytest.raises(FourDFD6AcceptanceError):
        positive(value, "company_id")
