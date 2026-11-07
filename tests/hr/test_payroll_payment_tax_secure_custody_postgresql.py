"""Company-authorized encrypted custody against real PostgreSQL."""

import base64
import hashlib

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_payment_tax_document_integrity_postgresql import (
    scenario as original_scenario,
    DOCUMENT,
)
from app.services.payroll_payment_tax_secure_custody import (
    store_encrypted_document,
    retrieve_encrypted_document,
    review_custodied_document,
)
from app.services.payroll_payment_tax_document_integrity import (
    record_document_integrity,
)
from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
)

pytestmark = pytest.mark.asyncio


def install(connection):
    import runpy
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    assert connection.exec_driver_sql(
        "SELECT current_database()"
    ).scalar_one() != "erp_db"

    migration = runpy.run_path(
        "alembic/versions/"
        "p13wtcust261107_encrypted_payment_tax_custody.py"
    )

    assert migration["revision"] == "p13wtcust261107"
    assert migration["down_revision"] == "p13wtint261107"

    assert connection.exec_driver_sql(
        "SELECT to_regclass("
        "'payroll_payment_tax_secure_custody'"
        ")"
    ).scalar_one() is None

    with Operations.context(
        MigrationContext.configure(connection)
    ):
        migration["upgrade"]()


async def grant_role(db, company, user, name):
    role = (
        await db.execute(
            text("""
                INSERT INTO roles (name)
                VALUES (:name)
                RETURNING id
            """),
            {"name": name},
        )
    ).scalar_one()

    for permission_name in (
        "employees.manage",
        "journal_entries.approve",
    ):
        permission = await db.scalar(
            text("""
                SELECT id FROM permissions
                WHERE name=:name
            """),
            {"name": permission_name},
        )
        if permission is None:
            # Fixture data ONLY in disposable PostgreSQL E2E.
            # Never seed or modify the production ERP database.
            current_db = await db.scalar(
                text("SELECT current_database()")
            )
            if current_db == "erp_db":
                raise RuntimeError(
                    "STOP: Cannot seed production permissions"
                )

            permission = (
                await db.execute(
                    text("""
                        INSERT INTO permissions (name)
                        VALUES (:name)
                        RETURNING id
                    """),
                    {"name": permission_name},
                )
            ).scalar_one()

        await db.execute(
            text("""
                INSERT INTO role_permissions (
                    role_id, permission_id
                ) VALUES (:role,:permission)
            """),
            {
                "role": role,
                "permission": permission,
            },
        )

    await db.execute(
        text("""
            INSERT INTO user_company_roles (
                user_id, company_id, role_id,
                is_active, assigned_at
            ) VALUES (
                :user, :company, :role,
                true, CURRENT_TIMESTAMP
            )
        """),
        {
            "user": user,
            "company": company,
            "role": role,
        },
    )


async def setup(db, monkeypatch):
    kwargs, candidate, evidence = await original_scenario(db)

    connection = await db.connection()
    await connection.run_sync(install)

    key = AESGCM.generate_key(bit_length=256)
    monkeypatch.setenv(
        "ERP_PAYROLL_CUSTODY_KEY_B64",
        base64.b64encode(key).decode(),
    )
    monkeypatch.setenv(
        "ERP_PAYROLL_CUSTODY_KEY_ID",
        "isolated-postgresql-test-key",
    )

    company = kwargs["company_id"]
    author = kwargs["created_by"]

    await grant_role(
        db, company, author, "custody_test_author"
    )

    reviewer = (
        await db.execute(
            text("""
                INSERT INTO users (
                    email, password_hash,
                    first_name, last_name,
                    is_active, created_at
                )
                SELECT
                    'custody-reviewer-' || id || '@example.invalid',
                    password_hash,
                    'Custody', 'Reviewer',
                    true, CURRENT_TIMESTAMP
                FROM users
                WHERE id=:author
                RETURNING id
            """),
            {"author": author},
        )
    ).scalar_one()

    await grant_role(
        db, company, reviewer, "custody_test_reviewer"
    )

    integrity = await record_document_integrity(
        db,
        company_id=company,
        evidence_id=evidence["evidence_id"],
        document_bytes=DOCUMENT,
        checked_by=author,
        request_key="custody-positive-integrity",
    )

    custody = await store_encrypted_document(
        db,
        company_id=company,
        evidence_id=evidence["evidence_id"],
        integrity_check_id=integrity["integrity_check_id"],
        document_bytes=DOCUMENT,
        created_by=author,
        request_key="custody-positive-write",
    )

    return company, author, reviewer, evidence, integrity, custody


async def test_pg_positive_authorized_review(
    employee_engine, monkeypatch,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        (
            company, author, reviewer,
            evidence, integrity, custody,
        ) = await setup(db, monkeypatch)

        restored = await retrieve_encrypted_document(
            db,
            company_id=company,
            evidence_id=evidence["evidence_id"],
            actor_id=reviewer,
        )
        assert restored == DOCUMENT

        result = await review_custodied_document(
            db,
            company_id=company,
            integrity_check_id=integrity["integrity_check_id"],
            evidence_id=evidence["evidence_id"],
            reviewed_by=reviewer,
            review_reference="INTERNAL_TEST_REVIEW",
            request_key="positive-custody-review",
        )

        assert result["authorized_company_review"]
        assert result["official_generation"] == "BLOCKED"
        assert not result["issuer_verified"]
        assert not result["actual_tax_withholding_verified"]

        with pytest.raises(
            PaymentTaxAllocationError,
            match="Independent reviewer",
        ):
            await review_custodied_document(
                db,
                company_id=company,
                integrity_check_id=integrity["integrity_check_id"],
                evidence_id=evidence["evidence_id"],
                reviewed_by=author,
                review_reference="SELF_REVIEW",
                request_key="rejected-self-review",
            )

        await db.rollback()


async def test_pg_denied_access_and_immutable_bytes(
    employee_engine, monkeypatch,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        (
            company, author, reviewer,
            evidence, integrity, custody,
        ) = await setup(db, monkeypatch)

        with pytest.raises(
            PaymentTaxAllocationError,
            match="Company permission denied",
        ):
            await retrieve_encrypted_document(
                db,
                company_id=company + 999999,
                evidence_id=evidence["evidence_id"],
                actor_id=reviewer,
            )

        for statement in (
            """
            UPDATE payroll_payment_tax_secure_custody
            SET ciphertext=decode('ff','hex')
            WHERE id=:id
            """,
            """
            DELETE FROM payroll_payment_tax_secure_custody
            WHERE id=:id
            """,
        ):
            with pytest.raises(DBAPIError):
                async with db.begin_nested():
                    await db.execute(
                        text(statement),
                        {"id": custody["custody_id"]},
                    )

        await db.rollback()
