"""Isolated PostgreSQL G7: persistence, tenant isolation and rollback."""
from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_source_snapshot_postgresql import prepare_case

from app.models.employee import Employee
from app.models.payroll_taxpayer_identity_review import (
    PayrollTaxpayerIdentityReview,
)
from app.services.payroll_taxpayer_identity_review_service import (
    TaxpayerIdentityReviewError,
    create_identity_review,
    decide_identity_review,
    require_reviewed_identity,
)

pytestmark = pytest.mark.asyncio


async def setup_case(db):
    company, user, calculation, report = await prepare_case(db)

    from app.models.employment_contract import EmploymentContract

    contract = await db.get(
        EmploymentContract,
        calculation.employment_contract_id,
    )
    employee = await db.get(Employee, contract.employee_id)

    employee.tax_number = "1234567890"
    await db.flush()

    review = await create_identity_review(
        db,
        company_id=company.id,
        employee_id=employee.id,
        tax_number="1234567890",
        evidence_reference="G7-ISOLATED-TEST-REVIEW",
        valid_from=date(2026, 1, 1),
        valid_to=None,
        created_by=user.id,
    )

    return company.id, employee.id, user.id, review.id


async def test_pending_evidence_persists_and_rejects_same_reviewer(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company_id, employee_id, creator_id, review_id = (
            await setup_case(db)
        )

        db.expire_all()

        stored = await db.scalar(
            select(PayrollTaxpayerIdentityReview)
            .where(
                PayrollTaxpayerIdentityReview.company_id
                    == company_id,
                PayrollTaxpayerIdentityReview.id == review_id,
            )
        )

        assert stored is not None
        assert stored.review_status == "pending"

        with pytest.raises(
            TaxpayerIdentityReviewError,
            match="Independent reviewer",
        ):
            await decide_identity_review(
                db,
                company_id=company_id,
                review_id=review_id,
                reviewed_by=creator_id,
                approved=True,
            )

        with pytest.raises(
            TaxpayerIdentityReviewError,
            match="Verified",
        ):
            await require_reviewed_identity(
                db,
                company_id=company_id,
                employee_id=employee_id,
                review_id=review_id,
                effective_on=date(2026, 10, 1),
                expected_tax_number="1234567890",
            )

        await db.rollback()


async def test_cross_company_isolation_and_rollback(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company_id, employee_id, creator_id, review_id = (
            await setup_case(db)
        )

        with pytest.raises(
            TaxpayerIdentityReviewError,
            match="not found",
        ):
            await decide_identity_review(
                db,
                company_id=company_id + 100000,
                review_id=review_id,
                reviewed_by=creator_id,
                approved=True,
            )

        await db.rollback()

    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        count = await db.scalar(
            select(func.count())
            .select_from(PayrollTaxpayerIdentityReview)
            .where(
                PayrollTaxpayerIdentityReview.id == review_id,
                PayrollTaxpayerIdentityReview.company_id == company_id,
            )
        )
        assert count == 0
