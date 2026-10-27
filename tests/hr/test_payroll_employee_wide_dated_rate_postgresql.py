"""Real PostgreSQL fail-closed employee-wide rate and input E2E."""

import os
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_employee_tax_base import setup_month

from app.models.payroll_statutory import (
    PayrollStatutoryRate,
    PayrollStatutoryComponent as Component,
    PayrollStatutoryResult as Result,
    PayrollStatutoryResultLine as Line,
)
from app.services.payroll_statutory_service import (
    calculate_payroll_statutory_result,
    PayrollStatutoryValidationError,
    PayrollStatutoryNotFoundError,
)


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_E2E") != "1",
        reason="Real isolated PostgreSQL required",
    ),
]


async def counts(db, company, calculations):
    ids = [item.id for item in calculations]
    return (
        await db.scalar(
            select(func.count(Result.id)).where(
                Result.company_id == company.id,
                Result.payroll_calculation_id.in_(ids),
            )
        ),
        await db.scalar(
            select(func.count(Line.id)).where(
                Line.company_id == company.id,
            )
        ),
    )


async def calculate(db, company, user, calculation):
    return await calculate_payroll_statutory_result(
        db,
        company_id=company.id,
        payroll_calculation_id=calculation.id,
        actor_user_id=user.id,
    )


@pytest.mark.parametrize(
    "component",
    [
        Component.PERSONAL_INCOME_TAX.value,
        Component.UNIFIED_SOCIAL_CONTRIBUTION.value,
    ],
)
@pytest.mark.parametrize("target_index", [0, 1])
async def test_uncovered_midmonth_statutory_rate_fail_closed(
    employee_engine, component, target_index
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, user, calculations = await setup_month(
            db, ("8000", "7000"), None, "10000"
        )

        rates = list((
            await db.scalars(
                select(PayrollStatutoryRate).where(
                    PayrollStatutoryRate.company_id == company.id,
                    PayrollStatutoryRate.component == component,
                )
            )
        ).all())

        assert len(rates) == 1, (
            "Expected exactly one test rate: "
            f"{component}; got {len(rates)}"
        )

        rate = rates[0]
        assert rate.effective_from == date(2026, 1, 1)

        baseline = await counts(db, company, calculations)
        assert baseline[0] == 0

        savepoint = await db.begin_nested()

        try:
            # Deliberately remove rate coverage for September 1-14.
            # This is an invalid input / incomplete policy window,
            # NOT a positive multiple-rate allocation.
            rate.effective_from = date(2026, 9, 15)
            await db.flush()

            print(
                "E2-C2 UNCOVERED RATE WINDOW SET = PASS",
                component, target_index
            )

            with pytest.raises(
                (
                    PayrollStatutoryValidationError,
                    PayrollStatutoryNotFoundError,
                ),
                match=(
                    "dated allocation|dated income allocation|"
                    "no statutory rate applies|rate changes|"
                    "rate change"
                ),
            ):
                await calculate(
                    db, company, user, calculations[target_index]
                )

            assert await counts(db, company, calculations) == baseline

            print(
                "E2-C2 RATE WINDOW FAIL CLOSED = PASS",
                component, target_index
            )
            print(
                "E2-C2 RATE ZERO PARTIAL WRITES = PASS",
                component, target_index
            )

        finally:
            if savepoint.is_active:
                await savepoint.rollback()

        await db.refresh(rate)
        assert rate.effective_from == date(2026, 1, 1)
        assert await counts(db, company, calculations) == baseline

        results = [
            await calculate(db, company, user, calculation)
            for calculation in reversed(calculations)
        ]

        assert results[0].id != results[1].id
        assert (await counts(db, company, calculations))[0] == 2

        print(
            "E2-C2 RATE RESTORE AND POSITIVE RETRY = PASS",
            component, target_index
        )

        await db.rollback()


@pytest.mark.parametrize("target_index", [0, 1])
@pytest.mark.parametrize(
    "invalid_case",
    ["currency", "revision"],
)
async def test_invalid_employee_calculation_has_no_partial_tax_result(
    employee_engine, target_index, invalid_case
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, user, calculations = await setup_month(
            db, ("8000", "7000"), None, "10000"
        )

        baseline = await counts(db, company, calculations)
        assert baseline[0] == 0

        changed = calculations[target_index]
        original_currency = changed.currency_code
        original_revision = changed.revision

        savepoint = await db.begin_nested()

        try:
            if invalid_case == "currency":
                changed.currency_code = "USD"
            else:
                changed.revision = 2

            await db.flush()

            with pytest.raises(
                PayrollStatutoryValidationError,
                match="UAH|correction is not linked",
            ):
                await calculate(
                    db, company, user, changed
                )

            assert await counts(db, company, calculations) == baseline

            print(
                "E2-C2 INVALID EMPLOYEE INPUT REJECTED = PASS",
                invalid_case, target_index
            )

            print(
                "E2-C2 INVALID INPUT ZERO LEAK = PASS",
                invalid_case, target_index
            )

        finally:
            if savepoint.is_active:
                await savepoint.rollback()

        await db.refresh(changed)
        assert changed.currency_code == original_currency
        assert changed.revision == original_revision
        assert await counts(db, company, calculations) == baseline

        results = [
            await calculate(db, company, user, calculation)
            for calculation in reversed(calculations)
        ]

        assert results[0].id != results[1].id

        print(
            "E2-C2 INVALID INPUT ROLLBACK RETRY = PASS",
            invalid_case, target_index
        )

        await db.rollback()
