"""Real PostgreSQL integrity and independent-review safeguards."""

import hashlib

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_payment_tax_evidence_postgresql import (
    prepare,
    evidence_args,
)
from app.services.payroll_payment_tax_evidence import (
    register_payment_tax_evidence,
)
from app.services.payroll_payment_tax_document_integrity import (
    record_document_integrity,
    record_independent_document_review,
)
from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
)


pytestmark = pytest.mark.asyncio

DOCUMENT = b"PAYROLL TEST EVIDENCE BYTES"


def migration_install(connection):
    import runpy

    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    database = connection.exec_driver_sql(
        "SELECT current_database()"
    ).scalar_one()

    if database == "erp_db":
        raise RuntimeError("STOP: Production DB")

    migration = runpy.run_path(
        "alembic/versions/"
        "p13wtint261107_payment_tax_document_integrity.py"
    )

    assert migration["revision"] == "p13wtint261107"
    assert migration["down_revision"] == "p13wtevid261107"

    existing = connection.exec_driver_sql(
        "SELECT to_regclass("
        "'payroll_payment_tax_document_integrity'"
        ")"
    ).scalar_one()

    if existing is not None:
        raise RuntimeError(
            "STOP: Integrity migration already installed"
        )

    with Operations.context(
        MigrationContext.configure(connection)
    ):
        migration["upgrade"]()

    reviews = connection.exec_driver_sql(
        "SELECT to_regclass("
        "'payroll_payment_tax_document_reviews'"
        ")"
    ).scalar_one()

    if reviews is None:
        raise RuntimeError(
            "STOP: Document review table missing"
        )


async def scenario(db):
    kwargs, candidate = await prepare(db)

    connection = await db.connection()
    await connection.run_sync(migration_install)

    params = evidence_args(
        kwargs,
        candidate,
        document_sha256=hashlib.sha256(DOCUMENT).hexdigest(),
    )

    evidence = await register_payment_tax_evidence(
        db, **params
    )

    return kwargs, candidate, evidence


async def test_pg_digest_match_and_immutability(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        kwargs, candidate, evidence = await scenario(db)

        record = await record_document_integrity(
            db,
            company_id=kwargs["company_id"],
            evidence_id=evidence["evidence_id"],
            document_bytes=DOCUMENT,
            request_key="integrity-1",
            checked_by=kwargs["created_by"],
        )

        assert record["status"] == "hash_matched_only"
        assert record["official_generation"] == "BLOCKED"
        assert not record["payment_specific_withholding_verified"]

        again = await record_document_integrity(
            db,
            company_id=kwargs["company_id"],
            evidence_id=evidence["evidence_id"],
            document_bytes=DOCUMENT,
            request_key="integrity-1",
            checked_by=kwargs["created_by"],
        )

        assert again["idempotent"]
        assert (
            again["integrity_check_id"]
            == record["integrity_check_id"]
        )

        with pytest.raises(
            PaymentTaxAllocationError,
            match="SHA-256 mismatch",
        ):
            await record_document_integrity(
                db,
                company_id=kwargs["company_id"],
                evidence_id=evidence["evidence_id"],
                document_bytes=b"ALTERED",
                request_key="integrity-altered",
                checked_by=kwargs["created_by"],
            )

        for sql in (
            """
            UPDATE payroll_payment_tax_document_integrity
            SET document_size_bytes=1
            WHERE id=:id
            """,
            """
            DELETE FROM payroll_payment_tax_document_integrity
            WHERE id=:id
            """,
        ):
            with pytest.raises(DBAPIError):
                async with db.begin_nested():
                    await db.execute(
                        text(sql),
                        {"id": record["integrity_check_id"]},
                    )

        await db.rollback()


async def test_pg_self_review_and_database_guard(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        kwargs, candidate, evidence = await scenario(db)

        integrity = await record_document_integrity(
            db,
            company_id=kwargs["company_id"],
            evidence_id=evidence["evidence_id"],
            document_bytes=DOCUMENT,
            request_key="integrity-review-test",
            checked_by=kwargs["created_by"],
        )

        with pytest.raises(
            PaymentTaxAllocationError,
            match="Independent reviewer",
        ):
            await record_independent_document_review(
                db,
                company_id=kwargs["company_id"],
                integrity_check_id=integrity["integrity_check_id"],
                reviewed_by=kwargs["created_by"],
                review_reference="SELF_REVIEW_TEST",
                request_key="self-review",
            )

        # Test bypassing the service directly:
        # PostgreSQL itself must reject self-review.
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(
                    text("""
                        INSERT INTO
                        payroll_payment_tax_document_reviews (
                            company_id, integrity_check_id,
                            evidence_id, reviewed_by,
                            review_reference, request_key,
                            review_status
                        ) VALUES (
                            :company, :check, :evidence,
                            :reviewer, 'TEST',
                            'direct-self-review',
                            'internal_review_only'
                        )
                    """),
                    {
                        "company": kwargs["company_id"],
                        "check": integrity["integrity_check_id"],
                        "evidence": evidence["evidence_id"],
                        "reviewer": kwargs["created_by"],
                    },
                )

        await db.rollback()
