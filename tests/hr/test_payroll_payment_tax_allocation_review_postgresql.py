"""Real PostgreSQL review/reversal event acceptance."""

import runpy

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_payment_tax_allocation_candidate_postgresql import (
    prepare_payment,
)
from app.services.payroll_payment_tax_allocation_candidate import (
    create_payment_tax_allocation_candidate,
    PaymentTaxAllocationError,
)
from app.services.payroll_payment_tax_allocation_review import (
    get_candidate_state,
    record_candidate_event,
)


pytestmark = pytest.mark.asyncio


def install_event_migration(connection):
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    database = connection.exec_driver_sql(
        "SELECT current_database()"
    ).scalar_one()

    if database == "erp_db":
        raise RuntimeError(
            "Refusing event migration in production ERP DB"
        )

    migration = runpy.run_path(
        "alembic/versions/"
        "p13wtrev261107_payment_tax_allocation_events.py"
    )

    assert migration["revision"] == "p13wtrev261107"
    assert migration["down_revision"] == "p13wtalloc261107"

    existing = connection.exec_driver_sql(
        "SELECT to_regclass("
        "'payroll_payment_tax_allocation_candidate_events'"
        ")"
    ).scalar_one()

    if existing is not None:
        # The shared payment fixture has already installed
        # the real migration in this isolated transaction.
        return

    with Operations.context(
        MigrationContext.configure(connection)
    ):
        migration["upgrade"]()

    assert connection.exec_driver_sql(
        "SELECT to_regclass("
        "'payroll_payment_tax_allocation_candidate_events'"
        ")"
    ).scalar_one() is not None


async def prepare(db):
    kwargs = await prepare_payment(db)

    connection = await db.connection()
    await connection.run_sync(install_event_migration)

    return kwargs


async def test_pg_revoke_and_block_reuse(employee_engine):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        kwargs = await prepare(db)

        candidate = await create_payment_tax_allocation_candidate(
            db, **kwargs
        )

        first = await record_candidate_event(
            db,
            company_id=kwargs["company_id"],
            candidate_id=candidate["id"],
            event_kind="revoked",
            request_key="tax-draft-revoke-1",
            evidence_reference="TEST_REVOCATION_ONLY",
            actor_user_id=kwargs["created_by"],
        )

        assert first["state"] == "revoked"
        assert first["event_count"] == 1
        assert not first["gross_paid_approved"]

        again = await record_candidate_event(
            db,
            company_id=kwargs["company_id"],
            candidate_id=candidate["id"],
            event_kind="revoked",
            request_key="tax-draft-revoke-1",
            evidence_reference="TEST_REVOCATION_ONLY",
            actor_user_id=kwargs["created_by"],
        )
        assert again["idempotent"]
        assert again["event_id"] == first["event_id"]

        with pytest.raises(
            PaymentTaxAllocationError,
            match="Revoked",
        ):
            await create_payment_tax_allocation_candidate(
                db, **kwargs
            )

        with pytest.raises(
            PaymentTaxAllocationError,
            match="Revoked",
        ):
            await record_candidate_event(
                db,
                company_id=kwargs["company_id"],
                candidate_id=candidate["id"],
                event_kind="reviewed",
                request_key="review-after-revoke",
                evidence_reference="TEST_ONLY",
                actor_user_id=kwargs["created_by"],
            )

        await db.rollback()


async def test_pg_append_only_and_self_review(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        kwargs = await prepare(db)

        candidate = await create_payment_tax_allocation_candidate(
            db, **kwargs
        )

        with pytest.raises(
            PaymentTaxAllocationError,
            match="Self-review",
        ):
            await record_candidate_event(
                db,
                company_id=kwargs["company_id"],
                candidate_id=candidate["id"],
                event_kind="reviewed",
                request_key="self-review-rejected",
                evidence_reference="TEST_ONLY",
                actor_user_id=kwargs["created_by"],
            )

        event = await record_candidate_event(
            db,
            company_id=kwargs["company_id"],
            candidate_id=candidate["id"],
            event_kind="revoked",
            request_key="immutability-revoke",
            evidence_reference="TEST_ONLY",
            actor_user_id=kwargs["created_by"],
        )

        for statement in (
            """
            DELETE FROM
            payroll_payment_tax_allocation_candidate_events
            WHERE id=:id
            """,
            """
            UPDATE
            payroll_payment_tax_allocation_candidate_events
            SET evidence_reference='ALTERED'
            WHERE id=:id
            """,
        ):
            with pytest.raises(DBAPIError):
                async with db.begin_nested():
                    await db.execute(
                        text(statement),
                        {"id": event["event_id"]},
                    )

        state = await get_candidate_state(
            db,
            company_id=kwargs["company_id"],
            candidate_id=candidate["id"],
        )

        assert state["state"] == "revoked"
        assert state["event_count"] == 1
        assert state["official_generation"] == "BLOCKED"

        await db.rollback()
