"""Positive custody operations after all seven security migrations.

Every database is disposable. Business writes run under erp_user.
This does not approve payment-specific withholding or official 4DF.
"""

import base64
import hashlib
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

import test_payroll_payment_tax_allocation_candidate_postgresql as candidate_pg

from test_employee_foundation import minimal_kwargs
from test_payroll_custody_role_security_postgresql import custody_engine
from test_payroll_payment_tax_secure_custody_postgresql import (
    DOCUMENT,
    grant_role,
)
from test_payroll_payment_tax_evidence_postgresql import evidence_args

from app.models.user import User
from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
    create_payment_tax_allocation_candidate,
)
from app.services.payroll_payment_tax_evidence import (
    register_payment_tax_evidence,
)
from app.services.payroll_payment_tax_document_integrity import (
    record_document_integrity,
)
from app.services.payroll_payment_tax_secure_custody import (
    store_encrypted_document,
    retrieve_encrypted_document,
    review_custodied_document,
)
from app.services.payroll_payment_tax_independent_source_audit import (
    audit_payment_tax_sources,
)
from app.services.payroll_custody_role_security import (
    require_custody_runtime_connection,
)

pytestmark = pytest.mark.asyncio


async def prepare_protected_business(
    engine,
    monkeypatch,
):
    # The seven real migrations are already applied by
    # custody_engine. The original payment helper's migration
    # installer must not run a second time.
    async def already_migrated(_db):
        return None

    monkeypatch.setattr(
        candidate_pg,
        "_initialize_disposable_schema",
        already_migrated,
    )

    key = AESGCM.generate_key(bit_length=256)
    monkeypatch.setenv(
        "ERP_PAYROLL_CUSTODY_KEY_B64",
        base64.b64encode(key).decode("ascii"),
    )
    monkeypatch.setenv(
        "ERP_PAYROLL_CUSTODY_KEY_ID",
        "positive-postmigration-disposable-only",
    )

    async with AsyncSession(
        engine, expire_on_commit=False
    ) as db:
        assert await db.scalar(
            text("SELECT current_user")
        ) == "erp_user"

        assert (
            await db.scalar(
                text("SELECT current_database()")
            )
        ).startswith("erp_custody_r5_")

        await require_custody_runtime_connection(db)

        kwargs = await candidate_pg.prepare_payment(db)

        candidate = await create_payment_tax_allocation_candidate(
            db, **kwargs
        )
        assert candidate["official_generation"] == "BLOCKED"

        digest = hashlib.sha256(DOCUMENT).hexdigest()
        evidence_params = evidence_args(
            kwargs,
            candidate,
            document_sha256=digest,
        )

        evidence = await register_payment_tax_evidence(
            db, **evidence_params
        )
        assert evidence["official_generation"] == "BLOCKED"
        assert not evidence["payment_specific_withholding_verified"]

        company = kwargs["company_id"]
        author = kwargs["created_by"]

        await grant_role(
            db,
            company,
            author,
            "positive_custody_author",
        )

        reviewer = User(
            **minimal_kwargs(
                User,
                {
                    "email": (
                        f"postmigration-reviewer-"
                        f"{uuid4().hex}@example.invalid"
                    ),
                    "first_name": "Custody",
                    "last_name": "Reviewer",
                },
            )
        )
        db.add(reviewer)
        await db.flush()

        await grant_role(
            db,
            company,
            reviewer.id,
            "positive_custody_reviewer",
        )

        integrity = await record_document_integrity(
            db,
            company_id=company,
            evidence_id=evidence["evidence_id"],
            document_bytes=DOCUMENT,
            checked_by=author,
            request_key="postmigration-integrity",
        )

        assert integrity["status"] == "hash_matched_only"
        assert integrity["official_generation"] == "BLOCKED"

        custody_params = dict(
            company_id=company,
            evidence_id=evidence["evidence_id"],
            integrity_check_id=integrity["integrity_check_id"],
            document_bytes=DOCUMENT,
            created_by=author,
            request_key="postmigration-custody",
        )

        custody = await store_encrypted_document(
            db, **custody_params
        )

        assert custody["idempotent"] is False
        assert custody["encryption"] == "AES-256-GCM"
        assert custody["official_generation"] == "BLOCKED"

        await db.commit()

    return {
        "company": company,
        "author": author,
        "reviewer": reviewer.id,
        "candidate": candidate,
        "evidence": evidence,
        "evidence_params": evidence_params,
        "integrity": integrity,
        "custody": custody,
        "custody_params": custody_params,
    }


async def test_positive_postmigration_write_read_review(
    custody_engine,
    monkeypatch,
):
    data = await prepare_protected_business(
        custody_engine, monkeypatch
    )

    company = data["company"]
    evidence_id = data["evidence"]["evidence_id"]
    integrity_id = data["integrity"]["integrity_check_id"]

    # A new session proves that prior business inserts committed.
    async with AsyncSession(
        custody_engine, expire_on_commit=False
    ) as db:
        await require_custody_runtime_connection(db)

        evidence_again = await register_payment_tax_evidence(
            db, **data["evidence_params"]
        )
        assert evidence_again["idempotent"] is True
        assert evidence_again["evidence_id"] == evidence_id

        integrity_again = await record_document_integrity(
            db,
            company_id=company,
            evidence_id=evidence_id,
            document_bytes=DOCUMENT,
            checked_by=data["author"],
            request_key="postmigration-integrity",
        )
        assert integrity_again["idempotent"] is True
        assert (
            integrity_again["integrity_check_id"]
            == integrity_id
        )

        custody_again = await store_encrypted_document(
            db, **data["custody_params"]
        )
        assert custody_again["idempotent"] is True
        assert (
            custody_again["custody_id"]
            == data["custody"]["custody_id"]
        )

        stored = (
            await db.execute(
                text("""
                    SELECT ciphertext, nonce, document_sha256
                    FROM public.payroll_payment_tax_secure_custody
                    WHERE company_id=:company
                      AND evidence_id=:evidence
                """),
                {"company": company, "evidence": evidence_id},
            )
        ).mappings().one()

        assert bytes(stored["ciphertext"]) != DOCUMENT
        assert len(bytes(stored["nonce"])) == 12
        assert stored["document_sha256"] == hashlib.sha256(
            DOCUMENT
        ).hexdigest()

        restored = await retrieve_encrypted_document(
            db,
            company_id=company,
            evidence_id=evidence_id,
            actor_id=data["reviewer"],
        )
        assert restored == DOCUMENT

        review_params = dict(
            company_id=company,
            integrity_check_id=integrity_id,
            evidence_id=evidence_id,
            reviewed_by=data["reviewer"],
            review_reference="POSTMIGRATION_INTERNAL_ONLY",
            request_key="postmigration-review",
        )

        review = await review_custodied_document(
            db, **review_params
        )

        assert review["authorized_company_review"] is True
        assert review["official_generation"] == "BLOCKED"
        assert not review["actual_tax_withholding_verified"]
        assert not review["issuer_verified"]

        repeated = await review_custodied_document(
            db, **review_params
        )

        assert repeated["idempotent"] is True
        assert repeated["review_id"] == review["review_id"]

        sources = await audit_payment_tax_sources(
            db,
            company_id=company,
            evidence_id=evidence_id,
        )
        assert sources["official_generation"] == "BLOCKED"
        assert sources["independent_actual_withholding"] == (
            "NOT_VERIFIED"
        )

        await db.commit()

    # Verify records survived another transaction boundary.
    async with AsyncSession(custody_engine) as db:
        count = await db.scalar(
            text("""
                SELECT count(*)
                FROM public.payroll_payment_tax_document_reviews
                WHERE company_id=:company
                  AND integrity_check_id=:integrity
            """),
            {
                "company": company,
                "integrity": integrity_id,
            },
        )
        assert count == 1


async def test_postmigration_negative_guards(
    custody_engine,
    monkeypatch,
):
    data = await prepare_protected_business(
        custody_engine, monkeypatch
    )

    async with AsyncSession(custody_engine) as db:
        with pytest.raises(
            PaymentTaxAllocationError,
            match="Company permission denied",
        ):
            await retrieve_encrypted_document(
                db,
                company_id=data["company"] + 999999,
                evidence_id=data["evidence"]["evidence_id"],
                actor_id=data["reviewer"],
            )

        with pytest.raises(
            PaymentTaxAllocationError,
            match="Independent reviewer",
        ):
            await review_custodied_document(
                db,
                company_id=data["company"],
                integrity_check_id=(
                    data["integrity"]["integrity_check_id"]
                ),
                evidence_id=data["evidence"]["evidence_id"],
                reviewed_by=data["author"],
                review_reference="DENIED_SELF_REVIEW",
                request_key="denied-self-review",
            )

        with pytest.raises(
            PaymentTaxAllocationError,
            match="digest differs",
        ):
            await store_encrypted_document(
                db,
                **{
                    **data["custody_params"],
                    "document_bytes": b"ALTERED DOCUMENT",
                    "request_key": "denied-altered-document",
                },
            )

        for table, row_id in (
            (
                "payroll_payment_tax_secure_custody",
                data["custody"]["custody_id"],
            ),
            (
                "payroll_payment_tax_document_integrity",
                data["integrity"]["integrity_check_id"],
            ),
        ):
            for statement in (
                f"UPDATE public.{table} "
                "SET id=id WHERE id=:id",
                f"DELETE FROM public.{table} "
                "WHERE id=:id",
            ):
                savepoint = await db.begin_nested()

                with pytest.raises(DBAPIError):
                    await db.execute(
                        text(statement),
                        {"id": row_id},
                    )

                await savepoint.rollback()

        state = await db.scalar(
            text("""
                SELECT count(*)
                FROM pg_trigger
                WHERE tgname='trg_p13_custody_immutable'
                  AND tgenabled IN ('O','A')
                  AND tgrelid IN (
                    to_regclass(
                      'public.payroll_payment_tax_secure_custody'
                    ),
                    to_regclass(
                      'public.payroll_payment_tax_document_integrity'
                    ),
                    to_regclass(
                      'public.payroll_payment_tax_document_reviews'
                    )
                  )
            """)
        )
        assert state == 3

        await db.rollback()
