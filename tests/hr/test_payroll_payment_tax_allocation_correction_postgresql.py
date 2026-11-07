"""Real PostgreSQL correction chain and negative acceptance."""

import runpy
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_payment_tax_allocation_candidate_postgresql import (
    prepare_payment,
)

from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
    create_payment_tax_allocation_candidate,
)
from app.services.payroll_payment_tax_allocation_review import (
    record_candidate_event,
)
from app.services.payroll_payment_tax_allocation_correction import (
    correct_payment_tax_allocation,
    effective_source_total,
)


pytestmark = pytest.mark.asyncio


def install_correction_migration(connection):
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    database = connection.exec_driver_sql(
        "SELECT current_database()"
    ).scalar_one()

    if database == "erp_db":
        raise RuntimeError("STOP: Production DB")

    migration = runpy.run_path(
        "alembic/versions/"
        "p13wtcorr261107_payment_tax_corrections.py"
    )

    assert migration["revision"] == "p13wtcorr261107"
    assert migration["down_revision"] == "p13wtrev261107"

    existing = connection.exec_driver_sql(
        "SELECT to_regclass("
        "'payroll_payment_tax_allocation_corrections'"
        ")"
    ).scalar_one()

    if existing is not None:
        # The shared fixture already applied this migration
        # inside the isolated test transaction.
        return

    with Operations.context(
        MigrationContext.configure(connection)
    ):
        migration["upgrade"]()

    assert connection.exec_driver_sql(
        "SELECT to_regclass("
        "'payroll_payment_tax_allocation_corrections'"
        ")"
    ).scalar_one() is not None


async def prepare(db, revoked=True):
    kwargs = await prepare_payment(db)

    connection = await db.connection()
    await connection.run_sync(
        install_correction_migration
    )

    candidate = await create_payment_tax_allocation_candidate(
        db, **kwargs
    )

    if revoked:
        await record_candidate_event(
            db,
            company_id=kwargs["company_id"],
            candidate_id=candidate["id"],
            event_kind="revoked",
            request_key="correction-revoke",
            evidence_reference="TEST_REVOKE",
            actor_user_id=kwargs["created_by"],
        )

    return kwargs, candidate


def correction_kwargs(kwargs, candidate, **overrides):
    values = dict(
        company_id=kwargs["company_id"],
        original_candidate_id=candidate["id"],
        previous_correction_id=None,
        amount=Decimal("0.02"),
        request_key="correction-1",
        evidence_reference="TEST_CORRECTION",
        created_by=kwargs["created_by"],
    )
    values.update(overrides)
    return values


async def test_pg_correction_chain_and_effective_total(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        kwargs, candidate = await prepare(db)
        first_kwargs = correction_kwargs(kwargs, candidate)

        first = await correct_payment_tax_allocation(
            db, **first_kwargs
        )
        assert first["effective_amount"] == "0.02"
        assert first["official_generation"] == "BLOCKED"

        again = await correct_payment_tax_allocation(
            db, **first_kwargs
        )
        assert again["idempotent"]
        assert again["correction_id"] == first["correction_id"]

        second = await correct_payment_tax_allocation(
            db, **correction_kwargs(
                kwargs, candidate,
                previous_correction_id=first["correction_id"],
                amount=Decimal("0.03"),
                request_key="correction-2",
            )
        )

        assert second["effective_amount"] == "0.03"

        total = await effective_source_total(
            db,
            company_id=kwargs["company_id"],
            statutory_line_id=kwargs["statutory_line_id"],
        )
        assert total == Decimal("0.03")

        with pytest.raises(
            PaymentTaxAllocationError,
            match="Stale",
        ):
            await correct_payment_tax_allocation(
                db, **correction_kwargs(
                    kwargs, candidate,
                    amount=Decimal("0.04"),
                    request_key="stale-correction",
                )
            )

        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(
                    text("""
                        UPDATE
                        payroll_payment_tax_allocation_corrections
                        SET amount=0.01 WHERE id=:id
                    """),
                    {"id": first["correction_id"]},
                )

        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(
                    text("""
                        DELETE FROM
                        payroll_payment_tax_allocation_candidates
                        WHERE id=:id
                    """),
                    {"id": candidate["id"]},
                )

        await db.rollback()


async def test_pg_correction_requires_revocation_and_ceiling(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        kwargs, candidate = await prepare(
            db, revoked=False
        )
        initial = correction_kwargs(kwargs, candidate)

        with pytest.raises(
            PaymentTaxAllocationError,
            match="revoked",
        ):
            await correct_payment_tax_allocation(
                db, **initial
            )

        await record_candidate_event(
            db,
            company_id=kwargs["company_id"],
            candidate_id=candidate["id"],
            event_kind="revoked",
            request_key="late-correction-revoke",
            evidence_reference="TEST_REVOKE",
            actor_user_id=kwargs["created_by"],
        )

        with pytest.raises(
            PaymentTaxAllocationError,
            match="exceeds",
        ):
            await correct_payment_tax_allocation(
                db, **correction_kwargs(
                    kwargs, candidate,
                    amount=Decimal("999999999.00"),
                    request_key="too-large",
                )
            )

        with pytest.raises(PaymentTaxAllocationError):
            await correct_payment_tax_allocation(
                db, **correction_kwargs(
                    kwargs, candidate,
                    company_id=kwargs["company_id"] + 999999,
                )
            )

        await db.rollback()
