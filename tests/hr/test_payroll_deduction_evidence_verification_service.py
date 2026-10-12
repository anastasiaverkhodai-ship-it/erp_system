"""Contract tests for payroll deduction evidence verification."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.payroll_deduction_evidence_verification_service import (
    PayrollDeductionEvidenceVerificationError,
    verify_deduction_evidence,
)


def make_evidence(status="pending"):
    evidence = MagicMock()
    evidence.id = 10
    evidence.company_id = 1
    evidence.legal_category = "enforcement_other"
    evidence.document_reference = "Court order 123"
    evidence.verification_status = status
    evidence.verified_by = None
    evidence.verified_at = None
    return evidence


def make_db(evidence):
    db = MagicMock()
    db.scalar = AsyncMock(return_value=evidence)
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_pending_evidence_can_be_verified():
    evidence = make_evidence()
    db = make_db(evidence)

    result = await verify_deduction_evidence(
        db=db,
        company_id=1,
        evidence_id=10,
        verified_by=5,
    )

    assert result is evidence
    assert evidence.verification_status == "verified"
    assert evidence.verified_by == 5
    assert evidence.verified_at is not None

    db.flush.assert_awaited_once()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_missing_evidence_rejected():
    db = make_db(None)

    with pytest.raises(
        PayrollDeductionEvidenceVerificationError,
        match="not found",
    ):
        await verify_deduction_evidence(
            db=db,
            company_id=1,
            evidence_id=10,
            verified_by=5,
        )

    db.flush.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    ["verified", "rejected"],
)
async def test_non_pending_evidence_rejected(status):
    evidence = make_evidence(status)
    db = make_db(evidence)

    with pytest.raises(
        PayrollDeductionEvidenceVerificationError,
        match="Only pending",
    ):
        await verify_deduction_evidence(
            db=db,
            company_id=1,
            evidence_id=10,
            verified_by=5,
        )

    db.flush.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "company_id,evidence_id,verifier",
    [
        (0, 10, 5),
        (1, 0, 5),
        (1, 10, 0),
    ],
)
async def test_invalid_identifiers_rejected(
    company_id,
    evidence_id,
    verifier,
):
    db = make_db(make_evidence())

    with pytest.raises(
        PayrollDeductionEvidenceVerificationError
    ):
        await verify_deduction_evidence(
            db=db,
            company_id=company_id,
            evidence_id=evidence_id,
            verified_by=verifier,
        )

    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_verification_query_uses_row_lock_and_company():
    db = make_db(None)

    with pytest.raises(
        PayrollDeductionEvidenceVerificationError
    ):
        await verify_deduction_evidence(
            db=db,
            company_id=7,
            evidence_id=10,
            verified_by=5,
        )

    statement = db.scalar.await_args.args[0]

    compiled = str(
        statement.compile(
            compile_kwargs={"literal_binds": True}
        )
    )

    assert "FOR UPDATE" in compiled
    assert "payroll_deduction_legal_evidence.company_id = 7" in compiled
    assert "payroll_deduction_legal_evidence.id = 10" in compiled
