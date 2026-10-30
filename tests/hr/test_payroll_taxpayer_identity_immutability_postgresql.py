"""Verify completed identity-review immutability in real PostgreSQL."""
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from payroll_g7_identity_trigger_fixture import g7_engine
from test_payroll_regulatory_4df_persisted_identity_postgresql import setup

from app.models.payroll_taxpayer_identity_review import (
    PayrollTaxpayerIdentityReview,
)
from app.services.payroll_taxpayer_identity_review_service import (
    decide_identity_review,
)

pytestmark = pytest.mark.asyncio


async def test_postgresql_trigger_rejects_real_update(g7_engine):
    async with AsyncSession(
        g7_engine, expire_on_commit=False
    ) as db:
        company_id, employee_id, calculation_id, reviewer_id, review_id = (
            await setup(db)
        )

        await decide_identity_review(
            db,
            company_id=company_id,
            review_id=review_id,
            reviewed_by=reviewer_id,
            approved=True,
        )

        # Check the persisted database value, not the ORM cache.
        stored = await db.execute(
            text("""
                SELECT review_status, reviewed_by, tax_number
                FROM payroll_taxpayer_identity_reviews
                WHERE id = :review_id AND company_id = :company_id
            """),
            {"review_id": review_id, "company_id": company_id},
        )
        row = stored.one_or_none()

        assert row is not None, (
            "ROOT CAUSE: Review row does not exist in PostgreSQL"
        )
        assert row.review_status == "verified", (
            "ROOT CAUSE: Review status not persisted: "
            + str(row.review_status)
        )
        assert row.reviewed_by == reviewer_id
        assert row.tax_number == "1234567890"

        # Alembic schema comparison does not validate triggers.
        triggers = (
            await db.execute(
                text("""
                    SELECT tgname, tgenabled,
                           pg_get_triggerdef(oid) AS definition
                    FROM pg_trigger
                    WHERE tgrelid =
                        'payroll_taxpayer_identity_reviews'::regclass
                      AND NOT tgisinternal
                """)
            )
        ).all()

        print("G7 USER TRIGGERS:", [
            (t.tgname, t.tgenabled) for t in triggers
        ])

        matching = [
            t for t in triggers
            if t.tgname == "trg_taxpayer_review_immutable"
        ]
        assert len(matching) == 1, (
            "ROOT CAUSE: Immutability trigger is missing"
        )
        assert matching[0].tgenabled in (b"O", b"A", "O", "A"), (
            "ROOT CAUSE: Immutability trigger is disabled"
        )

        print("G7 VERIFIED RECORD = PRESENT")
        print("G7 VERIFIED STATUS = PERSISTED")
        print("G7 IMMUTABILITY TRIGGER = ACTIVE")

        # A direct SQL UPDATE removes ORM synchronization ambiguity.
        # A failure must originate from PostgreSQL itself.
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                result = await db.execute(
                    text("""
                        UPDATE payroll_taxpayer_identity_reviews
                        SET tax_number = '9999999999'
                        WHERE id = :review_id
                          AND company_id = :company_id
                        RETURNING id
                    """),
                    {
                        "review_id": review_id,
                        "company_id": company_id,
                    },
                )
                updated = result.one_or_none()
                assert updated is not None, (
                    "ROOT CAUSE: UPDATE targeted no row"
                )
                pytest.fail(
                    "ROOT CAUSE: PostgreSQL allowed mutation "
                    "of verified identity review"
                )

        # Re-read persisted state after the failed SQL update.
        check = await db.execute(
            text("""
                SELECT review_status, tax_number
                FROM payroll_taxpayer_identity_reviews
                WHERE id = :review_id
                  AND company_id = :company_id
            """),
            {
                "review_id": review_id,
                "company_id": company_id,
            },
        )
        current = check.one()

        assert current.review_status == "verified"
        assert current.tax_number == "1234567890"

        print("G7 VERIFIED ROW MUTATION = REJECTED")
        print("G7 ORIGINAL TAX NUMBER = PRESERVED")

        await db.rollback()
