from test_employee_foundation import employee_engine, seed
"""Real PostgreSQL E2E for the trusted approval resolver.

Must execute only against the isolated PostgreSQL regression copy.
All fixture writes are rolled back.

This test does not authorize financial payroll deductions.
"""

import os
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.models.payroll_deduction_rule_approval import (
    PayrollDeductionRuleApproval,
)
from app.services.payroll_deduction_trusted_approval_service import (
    TrustedApprovalResolutionError,
    resolve_trusted_rule_approval,
)
from app.services.payroll_deduction_result_service import (
    PayrollDeductionResultConflictError,
    require_authorized_deduction_result,
)


@pytest.mark.asyncio
async def test_trusted_approval_real_postgresql_e2e(employee_engine):
    # Never run a fixture-writing test against the production/source DB.
    assert os.environ.get("RUN_POSTGRES_E2E") == "1", (
        "This test requires the isolated PostgreSQL runner"
    )

    database_url = os.environ.get("DATABASE_URL")
    assert database_url, "DATABASE_URL is required"
    url = make_url(database_url)

    assert url.get_backend_name() == "postgresql"
    assert url.database.startswith("erp_r3_copy_"), (
        "STOP: source PostgreSQL database is not isolated"
    )

    # Reuse the established PostgreSQL fixture.
    # The fixture owns the engine lifecycle.
    engine = employee_engine

    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()

            # Verify the actual connected database, not just the URL.
            connected_database = await connection.scalar(
                text("SELECT current_database()")
            )

            assert connected_database == url.database, (
                "STOP: fixture connected to unexpected database"
            )
            assert connected_database.startswith("erp_r3_copy_"), (
                "STOP: fixture is not using the isolated database"
            )

            try:
                async with AsyncSession(
                    bind=connection,
                    expire_on_commit=False,
                    autoflush=False,
                ) as db:

                    # Create isolated company/user fixtures.
                    # No real or pre-existing company IDs are assumed.
                    fixtures = await seed(db)

                    company_id = fixtures["company"]
                    creator_id = fixtures["linked_user"]
                    approver_id = fixtures["foreign_user"]

                    assert company_id is not None
                    assert creator_id is not None
                    assert approver_id is not None

                    # Four-eyes principle: independent identities.
                    assert creator_id != approver_id, (
                        "Creator and approver must differ"
                    )

                    now = datetime.now(timezone.utc)

                    # Start from a pending rule and transition to approved.
                    approval = PayrollDeductionRuleApproval(
                        company_id=company_id,
                        legal_category="enforcement_other",
                        rule_version="d1b3-e2e-isolated",
                        legal_reference="Isolated PostgreSQL E2E fixture",
                        valid_from=date(2026, 1, 1),
                        valid_to=date(2026, 12, 31),
                        status="pending",
                        created_by=creator_id,
                        created_at=now - timedelta(minutes=1),
                        approved_by=None,
                        approved_at=None,
                        revoked_by=None,
                        revoked_at=None,
                    )

                    db.add(approval)
                    await db.flush()

                    approval.status = "approved"
                    approval.approved_by = approver_id
                    approval.approved_at = now

                    await db.flush()

                    args = dict(
                        company_id=company_id,
                        legal_category="enforcement_other",
                        rule_version="d1b3-e2e-isolated",
                        effective_on=date(2026, 10, 13),
                    )

                    # 1. Real SQL query resolves an effective approval.
                    record = await resolve_trusted_rule_approval(
                        db,
                        **args,
                    )

                    assert record.company_id == company_id
                    assert record.category == "enforcement_other"
                    assert record.rule_version == "d1b3-e2e-isolated"
                    assert record.status.value == "approved"

                    # 2. Another company cannot use this approval.
                    with pytest.raises(
                        TrustedApprovalResolutionError
                    ):
                        await resolve_trusted_rule_approval(
                            db,
                            **{
                                **args,
                                "company_id": company_id + 1000000000,
                            },
                        )

                    # 3. Another rule version is rejected.
                    with pytest.raises(
                        TrustedApprovalResolutionError
                    ):
                        await resolve_trusted_rule_approval(
                            db,
                            **{
                                **args,
                                "rule_version": "unknown-version",
                            },
                        )

                    # 4. Approval is not valid before its start date.
                    with pytest.raises(
                        TrustedApprovalResolutionError
                    ):
                        await resolve_trusted_rule_approval(
                            db,
                            **{
                                **args,
                                "effective_on": date(2025, 12, 31),
                            },
                        )

                    # 5. Approval does not bypass execution guard.
                    with pytest.raises(
                        PayrollDeductionResultConflictError
                    ):
                        require_authorized_deduction_result(
                            SimpleNamespace(
                                deduction_amount=Decimal("100.00")
                            )
                        )

                    # 6. Revocation invalidates subsequent resolution.
                    approval.status = "revoked"
                    approval.revoked_by = approver_id
                    approval.revoked_at = now + timedelta(seconds=1)

                    await db.flush()

                    with pytest.raises(
                        TrustedApprovalResolutionError
                    ):
                        await resolve_trusted_rule_approval(
                            db,
                            **args,
                        )

                    print(
                        "REAL POSTGRESQL APPROVAL RESOLUTION = PASS"
                    )
                    print("COMPANY ISOLATION = PASS")
                    print("RULE VERSION = PASS")
                    print("EFFECTIVE DATE = PASS")
                    print("REVOCATION = PASS")
                    print("FINANCIAL EXECUTION GUARD = PASS")

            finally:
                # Never commit E2E fixture records.
                await transaction.rollback()

    finally:
        pass  # Engine lifecycle belongs to employee_engine fixture.
