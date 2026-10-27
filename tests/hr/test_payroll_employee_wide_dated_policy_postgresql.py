"""Fail-closed employee-wide mid-month tax-policy PostgreSQL E2E."""

import os
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_employee_tax_base import setup_month

from app.models.payroll_tax import PayrollStatutoryBaseRule
from app.models.payroll_statutory import (
    PayrollStatutoryComponent as Component,
    PayrollStatutoryResult as Result,
    PayrollStatutoryResultLine as Line,
)
from app.services.payroll_statutory_service import (
    PayrollStatutoryValidationError,
    calculate_payroll_statutory_result,
)


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_E2E") != "1",
        reason="Real isolated PostgreSQL required",
    ),
]


@pytest.mark.parametrize("target_index", [0, 1])
async def test_midmonth_employee_policy_requires_dated_allocation(
    employee_engine,
    target_index,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:

        company, user, calculations = await setup_month(
            db,
            ("8000", "7000"),
            None,
            "10000",
        )

        assert len(calculations) == 2

        async def result_count():
            return await db.scalar(
                select(func.count(Result.id)).where(
                    Result.company_id == company.id,
                    Result.payroll_calculation_id.in_(
                        [item.id for item in calculations]
                    ),
                )
            )

        async def line_count():
            return await db.scalar(
                select(func.count(Line.id)).where(
                    Line.company_id == company.id,
                )
            )

        before_results = await result_count()
        before_lines = await line_count()

        assert before_results == 0

        rule = await db.scalar(
            select(PayrollStatutoryBaseRule).where(
                PayrollStatutoryBaseRule.company_id == company.id,
                PayrollStatutoryBaseRule.component
                == Component.UNIFIED_SOCIAL_CONTRIBUTION.value,
                PayrollStatutoryBaseRule.employment_kind == "primary",
            )
        )

        assert rule is not None
        assert rule.effective_from == date(2026, 1, 1)

        original_date = rule.effective_from
        savepoint = await db.begin_nested()

        try:
            rule.effective_from = date(2026, 9, 15)
            await db.flush()

            assert rule.effective_from == date(2026, 9, 15)

            print(
                "E2-C1 MIDMONTH POLICY CHANGE SAVED = PASS",
                target_index,
            )

            with pytest.raises(
                PayrollStatutoryValidationError,
                match="dated allocation",
            ):
                await calculate_payroll_statutory_result(
                    db,
                    company_id=company.id,
                    payroll_calculation_id=calculations[target_index].id,
                    actor_user_id=user.id,
                )

            assert await result_count() == before_results
            assert await line_count() == before_lines

            print(
                "E2-C1 DATED POLICY REJECTION = PASS",
                target_index,
            )
            print(
                "E2-C1 ZERO PARTIAL STATUTORY WRITES = PASS",
                target_index,
            )

        finally:
            if savepoint.is_active:
                await savepoint.rollback()

        await db.refresh(rule)
        assert rule.effective_from == original_date

        assert await result_count() == before_results
        assert await line_count() == before_lines

        results = {}

        for index in (1, 0):
            results[index] = await calculate_payroll_statutory_result(
                db,
                company_id=company.id,
                payroll_calculation_id=calculations[index].id,
                actor_user_id=user.id,
            )

        assert results[0].id != results[1].id
        assert await result_count() == 2

        usc = []

        for index in (0, 1):
            line = await db.scalar(
                select(Line).where(
                    Line.company_id == company.id,
                    Line.payroll_statutory_result_id == results[index].id,
                    Line.component
                    == Component.UNIFIED_SOCIAL_CONTRIBUTION.value,
                )
            )
            assert line is not None
            usc.append(line)

        assert [line.base_amount for line in usc] == [
            Decimal("8000.00"),
            Decimal("2000.00"),
        ]

        assert sum(
            (line.amount for line in usc),
            Decimal("0.00"),
        ) == Decimal("2200.00")

        print(
            "E2-C1 RESTORED EMPLOYEE-WIDE USC = PASS",
            target_index,
        )

        await db.rollback()

        print(
            "E2-C1 FINAL POSTGRESQL ROLLBACK = PASS",
            target_index,
        )
