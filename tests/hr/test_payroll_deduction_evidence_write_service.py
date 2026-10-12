"""Unit contract tests for the pending evidence write service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.payroll_deduction_evidence_write_service import (
    PayrollDeductionEvidenceWriteError,
    create_pending_deduction_evidence,
)


def make_db(*scalar_results):
    db = MagicMock()
    db.scalar = AsyncMock(side_effect=scalar_results)
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_creates_pending_evidence():
    db = make_db(object(), None)

    evidence = await create_pending_deduction_evidence(
        db=db,
        company_id=1,
        payroll_deduction_instruction_id=10,
        legal_category="enforcement_other",
        document_reference=" Court order 123 ",
        created_by=5,
    )

    assert evidence.company_id == 1
    assert evidence.payroll_deduction_instruction_id == 10
    assert evidence.document_reference == "Court order 123"
    assert evidence.verification_status == "pending"
    assert evidence.verified_by is None
    assert evidence.verified_at is None

    db.add.assert_called_once_with(evidence)
    db.flush.assert_awaited_once()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_missing_instruction_rejected():
    db = make_db(None)

    with pytest.raises(
        PayrollDeductionEvidenceWriteError,
        match="not found",
    ):
        await create_pending_deduction_evidence(
            db=db,
            company_id=1,
            payroll_deduction_instruction_id=10,
            legal_category="enforcement_other",
            document_reference="Court order 123",
            created_by=5,
        )

    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_evidence_rejected():
    db = make_db(object(), object())

    with pytest.raises(
        PayrollDeductionEvidenceWriteError,
        match="already exists",
    ):
        await create_pending_deduction_evidence(
            db=db,
            company_id=1,
            payroll_deduction_instruction_id=10,
            legal_category="enforcement_other",
            document_reference="Court order 123",
            created_by=5,
        )

    db.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "company_id,instruction_id,actor",
    [
        (0, 10, 5),
        (1, 0, 5),
        (1, 10, 0),
    ],
)
async def test_invalid_identifiers_rejected(
    company_id,
    instruction_id,
    actor,
):
    db = make_db()

    with pytest.raises(PayrollDeductionEvidenceWriteError):
        await create_pending_deduction_evidence(
            db=db,
            company_id=company_id,
            payroll_deduction_instruction_id=instruction_id,
            legal_category="enforcement_other",
            document_reference="Court order 123",
            created_by=actor,
        )

    db.scalar.assert_not_awaited()
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_category_rejected():
    db = make_db()

    with pytest.raises(ValueError, match="Unsupported"):
        await create_pending_deduction_evidence(
            db=db,
            company_id=1,
            payroll_deduction_instruction_id=10,
            legal_category="unknown",
            document_reference="Court order 123",
            created_by=5,
        )

    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_instruction_query_is_company_scoped():
    db = make_db(None)

    with pytest.raises(PayrollDeductionEvidenceWriteError):
        await create_pending_deduction_evidence(
            db=db,
            company_id=7,
            payroll_deduction_instruction_id=10,
            legal_category="enforcement_other",
            document_reference="Court order 123",
            created_by=5,
        )

    query = db.scalar.await_args.args[0]

    compiled = str(
        query.compile(
            compile_kwargs={"literal_binds": True}
        )
    )

    assert "payroll_deduction_instructions.company_id = 7" in compiled
    assert "payroll_deduction_instructions.id = 10" in compiled
