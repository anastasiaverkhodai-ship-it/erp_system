"""Real PostgreSQL D5 source snapshot acceptance."""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_d5_database_field_projection_postgresql import (
    setup_case,
)
from test_payroll_d5_database_category_transition_postgresql import (
    setup_transition,
)
from test_payroll_d5_database_correction_chain_postgresql import (
    setup_correction,
)

from app.services.payroll_d5_verified_review_snapshot import (
    D5ReviewSnapshotError,
    build_d5_review_snapshot,
    verify_d5_review_snapshot,
)


pytestmark = pytest.mark.asyncio


async def test_pg_event_snapshot_integrity_and_source_change(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, actor, contract, employee, event, evidence = (
            await setup_case(db, "hire")
        )

        target = {
            "kind": "event",
            "employment_event_id": event.id,
        }

        snapshot = await build_d5_review_snapshot(
            db,
            company_id=company.id,
            targets=[target],
        )

        assert snapshot["body"]["entry_count"] == 1
        assert snapshot["body"]["official_generation"] == "BLOCKED"
        assert await verify_d5_review_snapshot(
            db, snapshot
        ) is True

        snapshot["body"]["official_rows_generated"] = 1

        with pytest.raises(
            D5ReviewSnapshotError,
            match="digest mismatch",
        ):
            await verify_d5_review_snapshot(
                db, snapshot
            )

        snapshot = await build_d5_review_snapshot(
            db,
            company_id=company.id,
            targets=[target],
        )

        event.order_number = "REVISED-" + uuid4().hex
        await db.flush()

        with pytest.raises(
            D5ReviewSnapshotError,
            match="no longer matches",
        ):
            await verify_d5_review_snapshot(
                db, snapshot
            )

        await db.rollback()


async def test_pg_historical_category_snapshot(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        (
            company, actor, contract, employee,
            transfer, current, previous, old,
        ) = await setup_transition(db)

        target = {
            "kind": "category_transition",
            "employment_event_id": transfer.id,
            "previous_evidence_id": old.id,
        }

        snapshot = await build_d5_review_snapshot(
            db,
            company_id=company.id,
            targets=[target],
        )

        assert await verify_d5_review_snapshot(
            db, snapshot
        ) is True

        old.profession_code = "9999.9"
        await db.flush()

        with pytest.raises(
            D5ReviewSnapshotError,
            match="no longer matches",
        ):
            await verify_d5_review_snapshot(
                db, snapshot
            )

        await db.rollback()


async def test_pg_correction_chain_snapshot(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        (
            company, actor, contract, original,
            reversal, replacement, evidence,
        ) = await setup_correction(db)

        target = {
            "kind": "correction_chain",
            "original_event_id": original.id,
            "reversal_event_id": reversal.id,
            "replacement_event_id": replacement.id,
        }

        snapshot = await build_d5_review_snapshot(
            db,
            company_id=company.id,
            targets=[target],
        )

        assert await verify_d5_review_snapshot(
            db, snapshot
        ) is True

        replacement.request_payload = {}
        await db.flush()

        with pytest.raises(
            D5ReviewSnapshotError,
            match="Live PostgreSQL",
        ):
            await verify_d5_review_snapshot(
                db, snapshot
            )

        await db.rollback()
