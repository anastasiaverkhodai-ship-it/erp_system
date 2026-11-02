from dataclasses import replace
from datetime import date

import pytest

from app.services.payroll_regulatory_normative_evidence import (
    NormativeEvidence,
    NormativeEvidenceError,
    evidence_for_period,
    fingerprint_source_bytes,
    normative_gate_status,
    validate_evidence,
)


@pytest.fixture
def evidence():
    return NormativeEvidence(
        evidence_id="TEST-D1-FIELD",
        report_kind="d1",
        field_key="TEST_ONLY_FIELD",
        source_title="Synthetic normative fixture",
        source_url="https://example.org/test-source",
        source_sha256=fingerprint_source_bytes(b"test document"),
        source_effective_from=date(2026, 1, 1),
        source_effective_to=None,
        reviewed_at=None,
        approved_at=None,
        approval_reference=None,
        state="candidate",
    )


def test_candidate_is_not_approved(evidence):
    validate_evidence(evidence)
    assert evidence_for_period(
        [evidence],
        report_kind="d1",
        field_key="TEST_ONLY_FIELD",
        period_start=date(2026, 10, 1),
        period_end=date(2026, 10, 31),
    ) is None


def test_source_verified_is_not_approved(evidence):
    record = replace(
        evidence,
        state="source_verified",
        reviewed_at=date(2026, 9, 1),
    )
    validate_evidence(record)
    assert evidence_for_period(
        [record],
        report_kind="d1",
        field_key="TEST_ONLY_FIELD",
        period_start=date(2026, 10, 1),
        period_end=date(2026, 10, 31),
    ) is None


def test_approval_requires_review_and_reference(evidence):
    with pytest.raises(NormativeEvidenceError):
        validate_evidence(replace(evidence, state="approved"))


def test_approved_fixture_can_cover_dates(evidence):
    approved = replace(
        evidence,
        state="approved",
        reviewed_at=date(2026, 9, 1),
        approved_at=date(2026, 9, 2),
        approval_reference="TEST_ONLY_NOT_PRODUCTION",
    )

    assert evidence_for_period(
        [approved],
        report_kind="d1",
        field_key="TEST_ONLY_FIELD",
        period_start=date(2026, 10, 1),
        period_end=date(2026, 10, 31),
    ) == approved


def test_effective_date_gap_rejected(evidence):
    approved = replace(
        evidence,
        state="approved",
        reviewed_at=date(2026, 9, 1),
        approved_at=date(2026, 9, 2),
        approval_reference="TEST_ONLY",
        source_effective_to=date(2026, 10, 15),
    )

    assert evidence_for_period(
        [approved],
        report_kind="d1",
        field_key="TEST_ONLY_FIELD",
        period_start=date(2026, 10, 1),
        period_end=date(2026, 10, 31),
    ) is None


def test_ambiguous_approvals_rejected(evidence):
    approved = replace(
        evidence,
        state="approved",
        reviewed_at=date(2026, 9, 1),
        approved_at=date(2026, 9, 2),
        approval_reference="TEST_ONLY",
    )

    with pytest.raises(
        NormativeEvidenceError,
        match="Ambiguous",
    ):
        evidence_for_period(
            [
                approved,
                replace(approved, evidence_id="OTHER"),
            ],
            report_kind="d1",
            field_key="TEST_ONLY_FIELD",
            period_start=date(2026, 10, 1),
            period_end=date(2026, 10, 31),
        )


@pytest.mark.parametrize("kind", ["d1", "4df", "d5", "d6"])
def test_each_form_starts_blocked(kind):
    result = normative_gate_status(
        [],
        start=date(2026, 10, 1),
        end=date(2026, 10, 31),
    )

    assert result["forms"][kind]["official_generation"] == "BLOCKED"
    assert result["forms"][kind]["approved_field_evidence"] == 0


def test_invalid_sha_rejected(evidence):
    with pytest.raises(
        NormativeEvidenceError,
        match="SHA256",
    ):
        validate_evidence(replace(
            evidence,
            source_sha256="not-a-real-source-hash",
        ))


def test_non_https_source_rejected(evidence):
    with pytest.raises(NormativeEvidenceError):
        validate_evidence(replace(
            evidence,
            source_url="http://example.org/source",
        ))


def test_empty_source_bytes_rejected():
    with pytest.raises(NormativeEvidenceError):
        fingerprint_source_bytes(b"")
