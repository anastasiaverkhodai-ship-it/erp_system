"""Unit tests for payroll tax documentary evidence."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.payroll_tax_evidence_service import (
    PayrollTaxEvidenceError,
    create_tax_evidence,
    get_tax_evidence,
    verify_tax_evidence,
    require_valid_tax_evidence,
)


def make_db(*, scalar_result=None):
    db = MagicMock()
    db.scalar = AsyncMock(return_value=scalar_result)
    db.flush = AsyncMock()
    db.add = MagicMock()
    return db


def make_evidence(**overrides):
    values = {
        "id": 10,
        "company_id": 1,
        "employee_id": 20,
        "entitlement_type": "benefit",
        "entitlement_code": "TEST_BENEFIT",
        "verification_status": "verified",
        "valid_from": date(2026, 1, 1),
        "valid_to": date(2026, 12, 31),
        "verified_by": 5,
        "verified_at": None,
    }
    values.update(overrides)
    row = SimpleNamespace(**values)
    row.created_by = 99
    return row



@pytest.mark.asyncio
async def test_create_pending_evidence():
    employee = SimpleNamespace(id=20, company_id=1)
    db = make_db(scalar_result=employee)

    row = await create_tax_evidence(
        db,
        company_id=1,
        employee_id=20,
        document_type="certificate",
        document_number="TEST-001",
        issued_on=date(2026, 1, 1),
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        entitlement_type="benefit",
        entitlement_code="TEST_BENEFIT",
        created_by=5,
    )

    assert row.verification_status == "pending"
    assert row.company_id == 1
    assert row.employee_id == 20

    db.add.assert_called_once()
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_rejects_foreign_employee():
    db = make_db(scalar_result=None)

    with pytest.raises(
        PayrollTaxEvidenceError,
        match="Employee not found",
    ):
        await create_tax_evidence(
            db,
            company_id=1,
            employee_id=999,
            document_type="certificate",
            document_number="TEST-002",
            issued_on=date(2026, 1, 1),
            valid_from=date(2026, 1, 1),
            valid_to=None,
            entitlement_type="benefit",
            entitlement_code="TEST_BENEFIT",
            created_by=5,
        )

    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_get_rejects_missing_evidence():
    db = make_db(scalar_result=None)

    with pytest.raises(
        PayrollTaxEvidenceError,
        match="not found in this company",
    ):
        await get_tax_evidence(
            db,
            company_id=1,
            evidence_id=999,
        )


@pytest.mark.asyncio
async def test_verify_pending_evidence():
    row = make_evidence(
        verification_status="pending",
        verified_by=None,
    )
    db = make_db(scalar_result=row)

    result = await verify_tax_evidence(
        db,
        company_id=1,
        evidence_id=10,
        verified_by=5,
        approved=True,
    )

    assert result.verification_status == "verified"
    assert result.verified_by == 5
    assert result.verified_at is not None

    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_reject_pending_evidence():
    row = make_evidence(
        verification_status="pending",
        verified_by=None,
    )
    db = make_db(scalar_result=row)

    result = await verify_tax_evidence(
        db,
        company_id=1,
        evidence_id=10,
        verified_by=5,
        approved=False,
    )

    assert result.verification_status == "rejected"
    assert result.verified_by == 5
    assert result.verified_at is not None


@pytest.mark.asyncio
async def test_cannot_verify_twice():
    row = make_evidence(
        verification_status="verified",
    )
    db = make_db(scalar_result=row)

    with pytest.raises(
        PayrollTaxEvidenceError,
        match="already been reviewed",
    ):
        await verify_tax_evidence(
            db,
            company_id=1,
            evidence_id=10,
            verified_by=5,
            approved=True,
        )


@pytest.mark.asyncio
async def test_pending_evidence_cannot_be_used():
    row = make_evidence(
        verification_status="pending",
    )
    db = make_db(scalar_result=row)

    with pytest.raises(
        PayrollTaxEvidenceError,
        match="not verified",
    ):
        await require_valid_tax_evidence(
            db,
            company_id=1,
            employee_id=20,
            evidence_id=10,
            entitlement_type="benefit",
            entitlement_code="TEST_BENEFIT",
            effective_on=date(2026, 6, 1),
        )


@pytest.mark.asyncio
async def test_evidence_cannot_be_used_for_another_employee():
    row = make_evidence(employee_id=20)
    db = make_db(scalar_result=row)

    with pytest.raises(
        PayrollTaxEvidenceError,
        match="another employee",
    ):
        await require_valid_tax_evidence(
            db,
            company_id=1,
            employee_id=21,
            evidence_id=10,
            entitlement_type="benefit",
            entitlement_code="TEST_BENEFIT",
            effective_on=date(2026, 6, 1),
        )


@pytest.mark.asyncio
async def test_evidence_type_must_match():
    db = make_db(scalar_result=make_evidence())

    with pytest.raises(
        PayrollTaxEvidenceError,
        match="type does not match",
    ):
        await require_valid_tax_evidence(
            db,
            company_id=1,
            employee_id=20,
            evidence_id=10,
            entitlement_type="exemption",
            entitlement_code="TEST_BENEFIT",
            effective_on=date(2026, 6, 1),
        )


@pytest.mark.asyncio
async def test_evidence_code_must_match():
    db = make_db(scalar_result=make_evidence())

    with pytest.raises(
        PayrollTaxEvidenceError,
        match="code does not match",
    ):
        await require_valid_tax_evidence(
            db,
            company_id=1,
            employee_id=20,
            evidence_id=10,
            entitlement_type="benefit",
            entitlement_code="WRONG_CODE",
            effective_on=date(2026, 6, 1),
        )


@pytest.mark.asyncio
async def test_expired_evidence_cannot_be_used():
    db = make_db(scalar_result=make_evidence())

    with pytest.raises(
        PayrollTaxEvidenceError,
        match="expired",
    ):
        await require_valid_tax_evidence(
            db,
            company_id=1,
            employee_id=20,
            evidence_id=10,
            entitlement_type="benefit",
            entitlement_code="TEST_BENEFIT",
            effective_on=date(2027, 1, 1),
        )


@pytest.mark.asyncio
async def test_verified_evidence_is_valid():
    row = make_evidence()
    db = make_db(scalar_result=row)

    result = await require_valid_tax_evidence(
        db,
        company_id=1,
        employee_id=20,
        evidence_id=10,
        entitlement_type="benefit",
        entitlement_code="TEST_BENEFIT",
        effective_on=date(2026, 6, 1),
    )

    assert result is row


@pytest.mark.asyncio
async def test_service_does_not_commit_or_rollback():
    employee = SimpleNamespace(id=20, company_id=1)
    db = make_db(scalar_result=employee)

    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    await create_tax_evidence(
        db,
        company_id=1,
        employee_id=20,
        document_type="certificate",
        document_number="TEST-003",
        issued_on=date(2026, 1, 1),
        valid_from=date(2026, 1, 1),
        valid_to=None,
        entitlement_type="benefit",
        entitlement_code="TEST_BENEFIT",
        created_by=5,
    )

    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_creator_cannot_review_own_evidence():
    """The document creator must not approve or reject it."""
    from app.services.payroll_tax_evidence_service import (
        PayrollTaxEvidenceError,
        verify_tax_evidence,
    )

    db = make_db()

    evidence = make_evidence()
    evidence.created_by = 5
    evidence.verification_status = "pending"

    db.scalar.return_value = evidence

    for approved in (True, False):
        with pytest.raises(
            PayrollTaxEvidenceError,
            match="cannot be reviewed by its creator",
        ):
            await verify_tax_evidence(
                db,
                company_id=evidence.company_id,
                evidence_id=evidence.id,
                verified_by=5,
                approved=approved,
            )

        assert evidence.verification_status == "pending"

    db.flush.assert_not_awaited()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()
