"""Positive real PostgreSQL employee-wide PIT/USC integration."""

import os
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_employee_tax_base import setup_month

from app.models.employment_contract import EmploymentContract
from app.models.payroll_statutory import (
    PayrollStatutoryComponent as Component,
    PayrollStatutoryResult as Result,
    PayrollStatutoryResultLine as Line,
)
from app.services.payroll_statutory_service import (
    calculate_payroll_statutory_result,
)


D = Decimal

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_E2E") != "1",
        reason="Real isolated PostgreSQL required",
    ),
]


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
async def test_employee_wide_two_contract_positive_pit_usc(order, employee_engine):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, user, calculations = await setup_month(
            db,
            ("8000", "7000"),
            None,
            "10000",
        )

        assert len(calculations) == 2

        first_contract = await db.get(
            EmploymentContract,
            calculations[0].employment_contract_id,
        )
        second_contract = await db.get(
            EmploymentContract,
            calculations[1].employment_contract_id,
        )

        assert first_contract is not None
        assert second_contract is not None
        assert first_contract.id != second_contract.id
        assert first_contract.employee_id == second_contract.employee_id
        assert first_contract.employment_kind == "primary"
        assert second_contract.employment_kind == "internal_secondary"

        print("E2-B2 SAME EMPLOYEE TWO CONTRACTS = PASS", order)

        results = {}

        for index in order:
            calculation = calculations[index]
            result = await calculate_payroll_statutory_result(
                db,
                company_id=company.id,
                payroll_calculation_id=calculation.id,
                actor_user_id=user.id,
            )
            await db.flush()
            assert result.payroll_calculation_id == calculation.id
            results[index] = result

        assert len(results) == 2
        assert results[0].id != results[1].id

        expected = {
            Component.PERSONAL_INCOME_TAX.value: (
                (D("8000.00"), D("1440.00")),
                (D("7000.00"), D("1260.00")),
            ),
            Component.MILITARY_LEVY.value: (
                (D("8000.00"), D("400.00")),
                (D("7000.00"), D("350.00")),
            ),
            Component.UNIFIED_SOCIAL_CONTRIBUTION.value: (
                (D("8000.00"), D("1760.00")),
                (D("2000.00"), D("440.00")),
            ),
        }

        expected_totals = {
            Component.PERSONAL_INCOME_TAX.value: D("2700.00"),
            Component.MILITARY_LEVY.value: D("750.00"),
            Component.UNIFIED_SOCIAL_CONTRIBUTION.value: D("2200.00"),
        }

        lines_by_calculation = {}

        for index in (0, 1):
            rows = list((
                await db.scalars(
                    select(Line).where(
                        Line.company_id == company.id,
                        Line.payroll_statutory_result_id
                        == results[index].id,
                    ).order_by(Line.line_no)
                )
            ).all())

            assert len(rows) == 3
            components = {row.component: row for row in rows}

            assert set(components) == set(expected)

            for component, values in expected.items():
                base, amount = values[index]
                row = components[component]
                assert row.base_amount == base, (
                    component,
                    index,
                    row.base_amount,
                    base,
                )
                assert row.amount == amount, (
                    component,
                    index,
                    row.amount,
                    amount,
                )
                assert row.employee_gross_amount == D("15000.00")
                assert row.currency_code == "UAH"

            lines_by_calculation[index] = rows

        for component, total_expected in expected_totals.items():
            total = sum(
                (
                    row.amount
                    for rows in lines_by_calculation.values()
                    for row in rows
                    if row.component == component
                ),
                D("0.00"),
            )
            assert total == total_expected

        print("E2-B2 PIT EMPLOYEE-WIDE TOTAL = PASS", order)
        print("E2-B2 USC EMPLOYEE-WIDE CAP = PASS", order)
        print("E2-B2 MILITARY LEVY TOTAL = PASS", order)
        print("E2-B2 ROUNDING AND CALL ORDER = PASS", order)

        before_count = await db.scalar(
            select(func.count(Result.id)).where(
                Result.company_id == company.id,
                Result.payroll_calculation_id.in_(
                    [calculation.id for calculation in calculations]
                ),
            )
        )
        assert before_count == 2

        for index in reversed(order):
            replay = await calculate_payroll_statutory_result(
                db,
                company_id=company.id,
                payroll_calculation_id=calculations[index].id,
                actor_user_id=user.id,
            )
            assert replay.id == results[index].id

        after_count = await db.scalar(
            select(func.count(Result.id)).where(
                Result.company_id == company.id,
                Result.payroll_calculation_id.in_(
                    [calculation.id for calculation in calculations]
                ),
            )
        )

        assert after_count == before_count == 2

        print("E2-B2 IDEMPOTENT SAVED RESULTS = PASS", order)

        await db.rollback()

        print("E2-B2 REAL POSTGRESQL ROLLBACK = PASS", order)
