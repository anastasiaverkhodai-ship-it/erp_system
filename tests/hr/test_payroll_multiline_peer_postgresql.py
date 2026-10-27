"""Real PostgreSQL: employee-wide peer lines must fail closed."""

import os

import pytest

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_employee_tax_base import setup_month

from app.models.payroll_statutory import (
    PayrollStatutoryResult as Result,
    PayrollStatutoryResultLine as Line,
    PayrollStatutoryComponent as Component,
)

from app.services.payroll_statutory_service import (
    calculate_payroll_statutory_result,
    PayrollStatutoryValidationError,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_E2E") != "1",
        reason="PostgreSQL required",
    ),
]


@pytest.mark.parametrize(
    "component",
    [
        Component.PERSONAL_INCOME_TAX,
        Component.UNIFIED_SOCIAL_CONTRIBUTION,
    ],
)
async def test_actual_duplicate_peer_component_rejected(
    employee_engine,
    component,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:

        company, user, calcs = await setup_month(
            db,
            ("8000", "7000"),
            None,
            "10000",
        )

        assert len(calcs) == 2

        peer = await calculate_payroll_statutory_result(
            db,
            company_id=company.id,
            payroll_calculation_id=calcs[1].id,
            actor_user_id=user.id,
        )

        await db.flush()

        before = await db.scalar(
            select(func.count(Result.id)).where(
                Result.company_id == company.id,
                Result.payroll_calculation_id == calcs[0].id,
            )
        )

        assert before == 0

        original = await db.scalar(
            select(Line).where(
                Line.company_id == company.id,
                Line.payroll_statutory_result_id == peer.id,
                Line.component == component.value,
            )
        )

        assert original is not None

        # Copy the actual persisted source provenance and tax
        # attributes. Do not invent FK IDs or policy metadata.
        values = {
            column.key: getattr(original, column.key)
            for column in Line.__table__.columns
            if column.key not in (
                "id",
                "line_no",
                "created_at",
            )
        }

        existing_numbers = list(
            (
                await db.scalars(
                    select(Line.line_no).where(
                        Line.company_id == company.id,
                        Line.payroll_statutory_result_id == peer.id,
                    )
                )
            ).all()
        )

        assert existing_numbers

        duplicate = Line(
            **values,
            line_no=max(existing_numbers) + 1,
        )

        db.add(duplicate)
        await db.flush()

        saved = list(
            (
                await db.scalars(
                    select(Line).where(
                        Line.company_id == company.id,
                        Line.payroll_statutory_result_id == peer.id,
                        Line.component == component.value,
                    ).order_by(Line.line_no)
                )
            ).all()
        )

        assert len(saved) == 2
        assert saved[0].id != saved[1].id
        assert saved[0].source_rate_id == saved[1].source_rate_id

        print(
            "REAL POSTGRESQL TWO STORED COMPONENT LINES = PASS",
            component.value,
        )

        with pytest.raises(
            PayrollStatutoryValidationError,
            match="Multiple peer statutory tax lines",
        ):
            await calculate_payroll_statutory_result(
                db,
                company_id=company.id,
                payroll_calculation_id=calcs[0].id,
                actor_user_id=user.id,
            )

        await db.flush()

        after = await db.scalar(
            select(func.count(Result.id)).where(
                Result.company_id == company.id,
                Result.payroll_calculation_id == calcs[0].id,
            )
        )

        assert after == before == 0

        still_saved = await db.scalar(
            select(func.count(Line.id)).where(
                Line.company_id == company.id,
                Line.payroll_statutory_result_id == peer.id,
                Line.component == component.value,
            )
        )

        assert still_saved == 2

        print(
            "PEER MULTILINE GUARD = PASS",
            component.value,
        )
        print(
            "NO PARTIAL TARGET STATUTORY RESULT = PASS",
            component.value,
        )

        await db.rollback()
