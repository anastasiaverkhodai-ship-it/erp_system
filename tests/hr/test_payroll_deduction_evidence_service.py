from datetime import datetime, timezone

import pytest

from app.services.payroll_deduction_evidence_service import (
    EvidenceStatus,
    PayrollDeductionEvidenceError,
    validate_evidence,
)


def test_pending_evidence():
    result = validate_evidence(
        legal_category="employer_statutory",
        document_reference=" Document 123 ",
        verification_status="pending",
        verified_by=None,
        verified_at=None,
    )

    assert result.document_reference == "Document 123"
    assert result.verification_status is EvidenceStatus.PENDING


def test_verified_evidence():
    result = validate_evidence(
        legal_category="minor_child_alimony",
        document_reference="Court document 123",
        verification_status="verified",
        verified_by=10,
        verified_at=datetime.now(timezone.utc),
    )

    assert result.verification_status is EvidenceStatus.VERIFIED


def test_verified_without_verifier_rejected():
    with pytest.raises(
        PayrollDeductionEvidenceError,
        match="verifier",
    ):
        validate_evidence(
            legal_category="enforcement_other",
            document_reference="Document 123",
            verification_status="verified",
            verified_by=None,
            verified_at=datetime.now(timezone.utc),
        )


def test_naive_verification_time_rejected():
    with pytest.raises(
        PayrollDeductionEvidenceError,
        match="timezone",
    ):
        validate_evidence(
            legal_category="enforcement_other",
            document_reference="Document 123",
            verification_status="verified",
            verified_by=10,
            verified_at=datetime(2026, 10, 12),
        )


def test_unverified_with_verifier_rejected():
    with pytest.raises(
        PayrollDeductionEvidenceError,
        match="Unverified",
    ):
        validate_evidence(
            legal_category="employer_statutory",
            document_reference="Document 123",
            verification_status="pending",
            verified_by=10,
            verified_at=None,
        )


def test_unknown_category_rejected():
    with pytest.raises(
        PayrollDeductionEvidenceError,
        match="Unsupported",
    ):
        validate_evidence(
            legal_category="unknown",
            document_reference="Document 123",
            verification_status="pending",
            verified_by=None,
            verified_at=None,
        )
