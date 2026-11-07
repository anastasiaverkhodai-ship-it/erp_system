"""Real PostgreSQL evidence intake and fail-closed tests."""

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
)
from app.services.payroll_payment_tax_evidence import (
    register_payment_tax_evidence,
)

pytestmark = pytest.mark.asyncio


def install_evidence_migration(connection):
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    database = connection.exec_driver_sql(
        "SELECT current_database()"
    ).scalar_one()

    if database == "erp_db":
        raise RuntimeError(
            "STOP: Refusing production migration"
        )

    migration = runpy.run_path(
        "alembic/versions/"
        "p13wtevid261107_payment_tax_evidence.py"
    )

    assert migration["revision"] == "p13wtevid261107"
    assert migration["down_revision"] == "p13wtcorr261107"

    assert connection.exec_driver_sql(
        "SELECT to_regclass("
        "'payroll_payment_tax_evidence'"
        ")"
    ).scalar_one() is None

    with Operations.context(
        MigrationContext.configure(connection)
    ):
        migration["upgrade"]()

    assert connection.exec_driver_sql(
        "SELECT to_regclass("
        "'payroll_payment_tax_evidence'"
        ")"
    ).scalar_one() is not None


def evidence_args(kwargs, candidate, **changes):
    values = dict(
        company_id=kwargs["company_id"],
        candidate_id=candidate["id"],
        correction_id=None,
        amount=Decimal("0.01"),
        source_type="test_external_statement",
        source_issuer="TEST_ONLY",
        evidence_reference="TEST_DOCUMENT_REFERENCE",
        document_sha256="a" * 64,
        request_key="test-evidence-1",
        created_by=kwargs["created_by"],
    )
    values.update(changes)
    return values


async def prepare(db):
    kwargs = await prepare_payment(db)
    connection = await db.connection()
    await connection.run_sync(install_evidence_migration)
    candidate = await create_payment_tax_allocation_candidate(
        db, **kwargs
    )
    return kwargs, candidate


async def test_pg_evidence_intake_idempotency_and_immutability(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        kwargs, candidate = await prepare(db)
        params = evidence_args(kwargs, candidate)

        first = await register_payment_tax_evidence(
            db, **params
        )
        assert first["verification_status"] == "unverified"
        assert first["official_generation"] == "BLOCKED"
        assert not first["payment_specific_withholding_verified"]

        again = await register_payment_tax_evidence(
            db, **params
        )
        assert again["idempotent"]
        assert again["evidence_id"] == first["evidence_id"]

        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(
                    text("""
                        UPDATE payroll_payment_tax_evidence
                        SET verification_status='verified'
                        WHERE id=:id
                    """),
                    {"id": first["evidence_id"]},
                )

        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(
                    text("""
                        DELETE FROM payroll_payment_tax_evidence
                        WHERE id=:id
                    """),
                    {"id": first["evidence_id"]},
                )

        await db.rollback()


async def test_pg_stale_evidence_and_amount_rejected(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        kwargs, candidate = await prepare(db)

        with pytest.raises(
            PaymentTaxAllocationError,
            match="amount differs",
        ):
            await register_payment_tax_evidence(
                db, **evidence_args(
                    kwargs, candidate,
                    amount=Decimal("0.02"),
                )
            )

        await record_candidate_event(
            db,
            company_id=kwargs["company_id"],
            candidate_id=candidate["id"],
            event_kind="revoked",
            request_key="evidence-test-revoke",
            evidence_reference="TEST_REVOCATION",
            actor_user_id=kwargs["created_by"],
        )

        correction = await correct_payment_tax_allocation(
            db,
            company_id=kwargs["company_id"],
            original_candidate_id=candidate["id"],
            previous_correction_id=None,
            amount=Decimal("0.02"),
            request_key="evidence-test-correction",
            evidence_reference="TEST_CORRECTION",
            created_by=kwargs["created_by"],
        )

        with pytest.raises(
            PaymentTaxAllocationError,
            match="stale correction",
        ):
            await register_payment_tax_evidence(
                db, **evidence_args(kwargs, candidate)
            )

        current = await register_payment_tax_evidence(
            db, **evidence_args(
                kwargs, candidate,
                correction_id=correction["correction_id"],
                amount=Decimal("0.02"),
                request_key="evidence-after-correction",
            )
        )

        assert current["correction_id"] == correction["correction_id"]
        assert current["verification_status"] == "unverified"
        assert current["official_generation"] == "BLOCKED"

        await db.rollback()
