"""Fail-closed contract tests for verified deduction evidence."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.payroll_deduction_evidence_policy import (
    PayrollDeductionEvidencePolicyError,
    resolve_verified_deduction_classification,
)


def make_evidence(
    status="verified",
    category="enforcement_other",
    reference="Court order 123",
    verifier=5,
):
    evidence = MagicMock()
    evidence.legal_category = category
    evidence.document_reference = reference
    evidence.verification_status = status
    evidence.verified_by = verifier
    evidence.verified_at = datetime.now(timezone.utc)
    return evidence


def make_db(evidence):
    db = MagicMock()
    db.scalar = AsyncMock(return_value=evidence)
    return db


@pytest.mark.asyncio
async def test_verified_evidence_resolves_classification():
    db = make_db(make_evidence())

    result = await resolve_verified_deduction_classification(
        db,
        company_id=1,
        payroll_deduction_instruction_id=10,
    )

    assert result.category == "enforcement_other"
    assert str(result.individual_limit_percentage) == "20"
    assert str(result.aggregate_limit_percentage) == "50"

    db.commit.assert_not_called()
    db.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_missing_evidence_rejected():
    db = make_db(None)

    with pytest.raises(
        PayrollDeductionEvidencePolicyError,
        match="required",
    ):
        await resolve_verified_deduction_classification(
            db,
            company_id=1,
            payroll_deduction_instruction_id=10,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "rejected"])
async def test_unverified_evidence_rejected(status):
    db = make_db(make_evidence(status=status))

    with pytest.raises(
        PayrollDeductionEvidencePolicyError,
        match="not verified",
    ):
        await resolve_verified_deduction_classification(
            db,
            company_id=1,
            payroll_deduction_instruction_id=10,
        )


@pytest.mark.asyncio
async def test_invalid_category_rejected():
    db = make_db(make_evidence(category="unknown"))

    with pytest.raises(
        PayrollDeductionEvidencePolicyError,
        match="Invalid verified",
    ):
        await resolve_verified_deduction_classification(
            db,
            company_id=1,
            payroll_deduction_instruction_id=10,
        )


@pytest.mark.asyncio
async def test_missing_verifier_rejected():
    db = make_db(make_evidence(verifier=None))

    with pytest.raises(
        PayrollDeductionEvidencePolicyError,
        match="Invalid verified",
    ):
        await resolve_verified_deduction_classification(
            db,
            company_id=1,
            payroll_deduction_instruction_id=10,
        )


@pytest.mark.asyncio
async def test_blank_reference_rejected():
    db = make_db(make_evidence(reference="  "))

    with pytest.raises(
        PayrollDeductionEvidencePolicyError,
        match="Invalid verified",
    ):
        await resolve_verified_deduction_classification(
            db,
            company_id=1,
            payroll_deduction_instruction_id=10,
        )


@pytest.mark.asyncio
async def test_query_is_company_scoped():
    db = make_db(None)

    with pytest.raises(PayrollDeductionEvidencePolicyError):
        await resolve_verified_deduction_classification(
            db,
            company_id=7,
            payroll_deduction_instruction_id=10,
        )

    statement = db.scalar.await_args.args[0]

    sql = str(
        statement.compile(
            compile_kwargs={"literal_binds": True}
        )
    )

    assert "payroll_deduction_legal_evidence.company_id = 7" in sql
    assert (
        "payroll_deduction_legal_evidence."
        "payroll_deduction_instruction_id = 10"
    ) in sql


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "company_id,instruction_id",
    [(0, 10), (1, 0)],
)
async def test_invalid_identifiers_rejected(
    company_id,
    instruction_id,
):
    db = make_db(None)

    with pytest.raises(PayrollDeductionEvidencePolicyError):
        await resolve_verified_deduction_classification(
            db,
            company_id=company_id,
            payroll_deduction_instruction_id=instruction_id,
        )

    db.scalar.assert_not_awaited()
