"""G7 positive approval and persisted 4DF identity PostgreSQL E2E."""
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from payroll_g7_identity_trigger_fixture import g7_engine
from test_payroll_regulatory_source_snapshot_postgresql import (
    prepare_case,
)

from app.models.employee import Employee
from app.models.user import User
from app.models.payroll_taxpayer_identity_review import (
    PayrollTaxpayerIdentityReview,
)
from app.services.payroll_taxpayer_identity_review_service import (
    TaxpayerIdentityReviewError,
    create_identity_review,
    decide_identity_review,
)
from app.services.payroll_regulatory_4df_persisted_identity import (
    attach_persisted_4df_taxpayer_identities,
)

pytestmark = pytest.mark.asyncio


async def second_user(db, original):
    """Clone an existing fixture user without assuming credential fields."""
    token = uuid4().hex[:12]
    data = {}
    mapper = inspect(User)

    for column in mapper.columns:
        if column.primary_key:
            continue

        name = column.key
        value = getattr(original, name)

        if name in ("created_at", "updated_at"):
            continue

        if isinstance(value, str) and (
            column.unique
            or name in (
                "email", "username", "login",
                "phone", "phone_number",
            )
        ):
            if name == "email":
                value = f"g7-{token}@example.invalid"
            else:
                value = f"g7-{token}"

            if column.type.length:
                value = value[:column.type.length]

        data[name] = value

    reviewer = User(**data)
    db.add(reviewer)
    await db.flush()

    assert reviewer.id != original.id
    return reviewer


async def setup(db):
    company, creator, calculation, report = (
        await prepare_case(db)
    )

    from app.models.employment_contract import (
        EmploymentContract,
    )

    contract = await db.get(
        EmploymentContract,
        calculation.employment_contract_id,
    )

    employee_id = contract.employee_id

    employee = await db.get(Employee, employee_id)
    employee.tax_number = "1234567890"

    await db.flush()

    reviewer = await second_user(db, creator)

    review = await create_identity_review(
        db,
        company_id=company.id,
        employee_id=employee_id,
        tax_number="1234567890",
        evidence_reference="G7-ISOLATED-DOCUMENT",
        valid_from=date(2026, 10, 1),
        valid_to=date(2026, 10, 31),
        created_by=creator.id,
    )

    return (
        company.id,
        employee_id,
        calculation.id,
        reviewer.id,
        review.id,
    )


def financial_source(company_id, employee_id, calculation_id):
    groups = [{
        "company_id": company_id,
        "employee_id": employee_id,
        "payroll_calculation_ids": [calculation_id],
        "income_month": "2026-10",
        "status": "REVIEW_ONLY_NOT_FOR_FILING",
        "statutory_approved": False,
        "gross_accrued": "1000.00",
        "paid_amount": "770.00",
    }]

    sources = [{
        "payroll_calculation_id": calculation_id,
        "employee_id": employee_id,
        "tax_number": "1234567890",
    }]

    return groups, sources


async def test_positive_independent_approval_and_persisted_binding(
    g7_engine,
):
    async with AsyncSession(
        g7_engine,
        expire_on_commit=False,
    ) as db:
        company_id, employee_id, calculation_id, reviewer_id, review_id = (
            await setup(db)
        )

        approved = await decide_identity_review(
            db,
            company_id=company_id,
            review_id=review_id,
            reviewed_by=reviewer_id,
            approved=True,
        )

        assert approved.review_status == "verified"
        assert approved.reviewed_by == reviewer_id
        assert approved.created_by != approved.reviewed_by

        groups, sources = financial_source(
            company_id, employee_id, calculation_id
        )

        result = await attach_persisted_4df_taxpayer_identities(
            db,
            company_id=company_id,
            financial_groups=groups,
            calculation_sources=sources,
            review_ids_by_employee={employee_id: review_id},
        )

        assert len(result) == 1
        identity = result[0]["taxpayer_identity"]

        assert identity["persisted_review_id"] == review_id
        assert identity["persistence"] == (
            "VERIFIED_POSTGRESQL_RECORD"
        )
        assert result[0]["statutory_approved"] is False

        await db.rollback()


async def test_pending_review_cannot_authorize_projection(
    g7_engine,
):
    async with AsyncSession(
        g7_engine,
        expire_on_commit=False,
    ) as db:
        company_id, employee_id, calculation_id, _, review_id = (
            await setup(db)
        )

        groups, sources = financial_source(
            company_id, employee_id, calculation_id
        )

        with pytest.raises(TaxpayerIdentityReviewError):
            await attach_persisted_4df_taxpayer_identities(
                db,
                company_id=company_id,
                financial_groups=groups,
                calculation_sources=sources,
                review_ids_by_employee={employee_id: review_id},
            )

        await db.rollback()


async def test_cross_company_review_cannot_authorize_projection(
    g7_engine,
):
    async with AsyncSession(
        g7_engine,
        expire_on_commit=False,
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

        groups, sources = financial_source(
            company_id + 1000,
            employee_id,
            calculation_id,
        )

        with pytest.raises(TaxpayerIdentityReviewError):
            await attach_persisted_4df_taxpayer_identities(
                db,
                company_id=company_id + 1000,
                financial_groups=groups,
                calculation_sources=sources,
                review_ids_by_employee={employee_id: review_id},
            )

        await db.rollback()


async def test_verified_identity_is_database_immutable(
    g7_engine,
):
    async with AsyncSession(
        g7_engine,
        expire_on_commit=False,
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

        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(
                    update(PayrollTaxpayerIdentityReview)
                    .where(
                        PayrollTaxpayerIdentityReview.id == review_id
                    )
                    .values(tax_number="9999999999")
                )

        await db.rollback()
