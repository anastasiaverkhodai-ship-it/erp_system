"""Real PostgreSQL D5/D6 regulatory bridge acceptance."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_d5_database_field_projection_postgresql import (
    setup_case,
)

from app.models.payroll import PayrollPeriod

from app.services.payroll_d5_regulatory_review_bridge import (
    D5RegulatoryBridgeError,
    build_d5_regulatory_review_bridge,
    verify_d5_regulatory_review_bridge,
)


pytestmark = pytest.mark.asyncio


async def setup_bridge(db):
    company, actor, contract, employee, event, evidence = (
        await setup_case(db, "hire")
    )

    period = await db.scalar(
        select(PayrollPeriod).where(
            PayrollPeriod.company_id == company.id
        ).order_by(PayrollPeriod.id)
    )

    assert period is not None

    return company, event, evidence, period


async def test_pg_regulatory_bridge_live_source_check(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, event, evidence, period = (
            await setup_bridge(db)
        )

        kwargs = {
            "company_id": company.id,
            "payroll_period_id": period.id,
            "targets": [{
                "kind": "event",
                "employment_event_id": event.id,
            }],
        }

        snapshot = await build_d5_regulatory_review_bridge(
            db, **kwargs
        )

        assert snapshot["body"]["official_generation"] == (
            "BLOCKED"
        )
        assert snapshot["body"]["official_rows_generated"] == 0
        assert snapshot["body"]["d5_target_count"] == 1

        assert await verify_d5_regulatory_review_bridge(
            db, snapshot
        ) is True

        evidence.profession_code = "9999.9"
        await db.flush()

        with pytest.raises(
            D5RegulatoryBridgeError,
            match="no longer matches",
        ):
            await verify_d5_regulatory_review_bridge(
                db, snapshot
            )

        await db.rollback()


async def test_pg_regulatory_bridge_tampering_and_scope(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, event, evidence, period = (
            await setup_bridge(db)
        )

        kwargs = {
            "company_id": company.id,
            "payroll_period_id": period.id,
            "targets": [{
                "kind": "event",
                "employment_event_id": event.id,
            }],
        }

        snapshot = await build_d5_regulatory_review_bridge(
            db, **kwargs
        )

        snapshot["body"]["official_generation"] = "ENABLED"

        with pytest.raises(
            D5RegulatoryBridgeError,
            match="integrity digest mismatch",
        ):
            await verify_d5_regulatory_review_bridge(
                db, snapshot
            )

        with pytest.raises(
            D5RegulatoryBridgeError,
            match="verification rejected",
        ):
            await build_d5_regulatory_review_bridge(
                db,
                company_id=company.id + 100000000,
                payroll_period_id=period.id,
                targets=kwargs["targets"],
            )

        await db.rollback()
