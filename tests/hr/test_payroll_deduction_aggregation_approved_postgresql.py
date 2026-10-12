
"""Phase 13 C2 — Real PostgreSQL approval aggregation E2E.

Requires RUN_POSTGRES_E2E=1.

Creates a temporary approved rule in a rollback-only transaction.
Uses a real PostgreSQL AsyncSession, not mocked query results.

This is NOT legal approval of an actual deduction instruction.
No payroll settlement, accounting posting or remittance occurs.
"""

import os
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import engine as configured_engine

from app.models.payroll_deduction_rule_approval import (
    PayrollDeductionRuleApproval,
)

from app.services.payroll_deduction_aggregation_policy import (
    DeductionPolicyLine,
)

from app.services.payroll_deduction_aggregation_db_integration import (
    DeductionApprovalResolutionError,
    PersistedDeductionLine,
    preview_deduction_plan_from_db,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_E2E") != "1",
    reason="Set RUN_POSTGRES_E2E=1",
)


def approved_status_value():
    """Use the status representation defined by the ORM model."""

    column = PayrollDeductionRuleApproval.__table__.c.status
    sql_type = column.type

    enum_class = getattr(sql_type, "enum_class", None)

    if enum_class is not None:
        members = getattr(enum_class, "__members__", {})

        if "APPROVED" in members:
            return members["APPROVED"]

        for member in enum_class:
            if str(member.value).lower() == "approved":
                return member

        raise AssertionError(
            "ORM status enum has no APPROVED value"
        )

    enum_values = getattr(sql_type, "enums", None)

    if enum_values:
        for value in enum_values:
            if str(value).lower() == "approved":
                return value

    # Common SQL VARCHAR status representation.
    # The real database constraint remains authoritative.
    return "approved"


@pytest.mark.asyncio
async def test_real_postgresql_approved_aggregation_rollback():
    assert os.getenv("RUN_POSTGRES_E2E") == "1"

    url = configured_engine.url

    # This E2E may insert rollback-only fixtures.
    # Never execute it against a remote database.
    if url.host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail(
            "E2E requires local PostgreSQL; "
            f"configured host is {url.host!r}"
        )

    if any(
        value in (url.database or "").lower()
        for value in ("production", "prod_db", "live_db")
    ):
        pytest.fail(
            "E2E refuses a production-like database name"
        )

    engine = create_async_engine(
        url,
        poolclass=NullPool,
        echo=False,
    )

    temporary_id = None

    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()

            try:
                async with AsyncSession(
                    bind=conn,
                    expire_on_commit=False,
                    autoflush=False,
                ) as db:

                    # Discover existing fixture identities.
                    # These SELECT queries never change data.
                    company_id = (
                        await db.execute(
                            text(
                                "SELECT id FROM companies "
                                "ORDER BY id LIMIT 1"
                            )
                        )
                    ).scalar_one_or_none()

                    user_ids = (
                        await db.execute(
                            text(
                                "SELECT id FROM users "
                                "ORDER BY id LIMIT 2"
                            )
                        )
                    ).scalars().all()

                    if company_id is None:
                        pytest.fail(
                            "No company fixture in local PostgreSQL"
                        )

                    if not user_ids:
                        pytest.fail(
                            "No user fixture in local PostgreSQL"
                        )

                    created_by = user_ids[0]
                    approved_by = (
                        user_ids[1]
                        if len(user_ids) > 1
                        else user_ids[0]
                    )

                    approval = PayrollDeductionRuleApproval(
                        company_id=company_id,
                        legal_category="employer_statutory",
                        rule_version=(
                            "C2-E2E-" + uuid4().hex[:16]
                        ),
                        legal_reference=(
                            "ROLLBACK-ONLY-C2-TEST-NOT-LEGAL"
                        ),
                        valid_from=date(2026, 1, 1),
                        valid_to=None,
                        status="pending",
                        created_by=created_by,
                        created_at=datetime.now(timezone.utc),
                        approved_by=None,
                        approved_at=None,
                    )

                    db.add(approval)

                    # INSERT happens inside the open transaction.
                    # There is deliberately no commit.
                    await db.flush()

                    temporary_id = approval.id

                    assert temporary_id is not None

                    # Step 1: the database accepted a PENDING row.
                    assert str(approval.status).lower() == "pending"

                    print("PENDING APPROVAL INSERT = PASS")

                    # Step 2: use the authorized lifecycle transition.
                    # This exercises the real PostgreSQL UPDATE trigger.
                    approval.status = approved_status_value()
                    approval.approved_by = approved_by
                    approval.approved_at = datetime.now(timezone.utc)

                    await db.flush()

                    print("PENDING -> APPROVED TRANSITION = PASS")

                    policy_line = DeductionPolicyLine(
                        instruction_id=1,
                        legal_category="employer_statutory",
                        priority=1,
                        requested_amount=Decimal("50.00"),
                        individual_limit=Decimal("40.00"),
                    )

                    persisted_line = PersistedDeductionLine(
                        approval_id=temporary_id,
                        policy_line=policy_line,
                    )

                    async def preview(
                        company,
                        effective_on,
                        lines=(persisted_line,),
                    ):
                        return await preview_deduction_plan_from_db(
                            db=db,
                            company_id=company,
                            effective_on=effective_on,
                            available_amount=Decimal("100.00"),
                            aggregate_limit=Decimal("30.00"),
                            lines=lines,
                        )

                    effective_on = date(2026, 10, 12)

                    # POSITIVE E2E:
                    # Fetch the real temporary approval row,
                    # validate it, then calculate the preview.
                    result = await preview(
                        company_id,
                        effective_on,
                    )

                    calculation = result.policy_preview.result

                    assert calculation.total_allocated == (
                        Decimal("30.00")
                    )

                    assert calculation.total_unallocated == (
                        Decimal("20.00")
                    )

                    assert len(calculation.allocations) == 1

                    allocation = calculation.allocations[0]

                    assert allocation.instruction_id == 1

                    assert allocation.allocated_amount == (
                        Decimal("30.00")
                    )

                    assert allocation.remaining_amount == (
                        Decimal("20.00")
                    )

                    assert result.payroll_posting_allowed is False

                    assert result.legal_authorization == (
                        "NOT_AUTHORIZED"
                    )

                    print("REAL APPROVED ROW LOOKUP = PASS")
                    print("APPROVED RULE CONTRACT = PASS")
                    print("ALLOCATION 30.00 = PASS")
                    print("REMAINDER 20.00 = PASS")
                    print("PAYROLL POSTING DISABLED = PASS")

                    # NEGATIVE E2E:
                    # The approval must not resolve for a
                    # different company.
                    with pytest.raises(
                        DeductionApprovalResolutionError
                    ):
                        await preview(
                            company_id + 1000000000,
                            effective_on,
                        )

                    print("COMPANY ISOLATION = PASS")

                    # The approval is valid from 2026-01-01.
                    # Earlier effective dates must fail.
                    with pytest.raises(ValueError):
                        await preview(
                            company_id,
                            date(2025, 12, 31),
                        )

                    print("APPROVAL EFFECTIVE DATE = PASS")

                    missing = PersistedDeductionLine(
                        approval_id=2147483647,
                        policy_line=policy_line,
                    )

                    with pytest.raises(
                        DeductionApprovalResolutionError
                    ):
                        await preview(
                            company_id,
                            effective_on,
                            (missing,),
                        )

                    print("MISSING APPROVAL = BLOCKED")

            finally:
                if transaction.is_active:
                    await transaction.rollback()

        assert temporary_id is not None

        # A separate connection proves that the temporary
        # approved row was not persisted.
        async with engine.connect() as verify_conn:
            remaining = (
                await verify_conn.execute(
                    select(
                        PayrollDeductionRuleApproval.id
                    ).where(
                        PayrollDeductionRuleApproval.id
                        == temporary_id
                    )
                )
            ).scalar_one_or_none()

        assert remaining is None, (
            "Temporary approval leaked after rollback"
        )

        print("ROLLBACK ZERO-LEAK = PASS")
        print("REAL POSTGRESQL APPROVED-ROW E2E = PASS")

    finally:
        await engine.dispose()
