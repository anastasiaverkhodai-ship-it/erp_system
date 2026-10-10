"""Contract tests for payroll tax evidence review audit."""

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import CheckConstraint

from app.models.payroll_employee_tax_evidence import (
    PayrollEmployeeTaxEvidence,
)
from app.services.payroll_tax_evidence_service import (
    verify_tax_evidence,
)


def make_db(row):
    db = MagicMock()
    db.scalar = AsyncMock(return_value=row)
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "approved,expected_status",
    [
        (True, "verified"),
        (False, "rejected"),
    ],
)
async def test_review_decision_records_actor_and_time(
    approved,
    expected_status,
):
    row = SimpleNamespace(
        id=10,
        company_id=1,
        employee_id=20,
        created_by=99,
        verification_status="pending",
        verified_by=None,
        verified_at=None,
    )

    db = make_db(row)

    before = datetime.now(timezone.utc)

    result = await verify_tax_evidence(
        db,
        company_id=1,
        evidence_id=10,
        verified_by=5,
        approved=approved,
    )

    after = datetime.now(timezone.utc)

    assert result.verification_status == expected_status
    assert result.verified_by == 5
    assert before <= result.verified_at <= after

    db.flush.assert_awaited_once()


def test_orm_review_constraint_covers_all_states():
    constraints = [
        constraint
        for constraint
        in PayrollEmployeeTaxEvidence.__table__.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name
        == "ck_payroll_tax_evidence_verification"
    ]

    assert len(constraints) == 1

    sql = str(constraints[0].sqltext)

    assert "verification_status = 'pending'" in sql
    assert "'verified', 'rejected'" in sql
    assert "verified_by IS NOT NULL" in sql
    assert "verified_at IS NOT NULL" in sql
