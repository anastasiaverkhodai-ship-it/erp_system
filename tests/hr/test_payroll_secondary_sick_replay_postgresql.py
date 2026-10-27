
"""PostgreSQL replay and conflicting-evidence invariants."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_secondary_sick_postgresql import prepare

from app.models.payroll_sick_leave import PayrollSickLeaveCalculation
from app.services.payroll_sick_history_service import (
    calculate_sick_from_history,
    PayrollSickLeaveConflictError,
)

pytestmark = pytest.mark.asyncio


async def count_calculations(db, company_id, contract_id):
    return await db.scalar(
        select(func.count())
        .select_from(PayrollSickLeaveCalculation)
        .where(
            PayrollSickLeaveCalculation.company_id == company_id,
            PayrollSickLeaveCalculation.employment_contract_id
            == contract_id,
        )
    )


async def test_replay_preserves_existing_calculation(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, primary, secondary, histories, args = (
            await prepare(db)
        )

        first = await calculate_sick_from_history(
            db, **args
        )
        await db.flush()

        first_id = first.id
        first_amount = first.sick_pay_amount
        first_assessment = first.insurance_assessment

        before = await count_calculations(
            db, company.id, secondary.id
        )

        assert before == 1

        repeated = await calculate_sick_from_history(
            db, **args
        )
        await db.flush()

        after = await count_calculations(
            db, company.id, secondary.id
        )

        assert repeated.id == first_id
        assert repeated.sick_pay_amount == first_amount
        assert repeated.insurance_assessment == first_assessment
        assert after == before == 1


async def test_changed_evidence_replay_rejected_without_mutation(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, primary, secondary, histories, args = (
            await prepare(db)
        )

        first = await calculate_sick_from_history(
            db, **args
        )
        await db.flush()

        first_id = first.id
        first_amount = first.sick_pay_amount
        first_evidence = first.insurance_evidence_id
        first_assessment = first.insurance_assessment

        # An intentionally different identifier tests the conflict
        # boundary. It does not claim a correction was performed.
        changed_evidence_id = (
            args["insurance_evidence_id"] + 1000000
        )

        with pytest.raises(
            PayrollSickLeaveConflictError,
            match="different evidence",
        ):
            await calculate_sick_from_history(
                db,
                **{
                    **args,
                    "insurance_evidence_id": changed_evidence_id,
                },
            )

        await db.flush()

        persisted = await db.get(
            PayrollSickLeaveCalculation, first_id
        )

        assert persisted is not None
        assert persisted.insurance_evidence_id == first_evidence
        assert persisted.sick_pay_amount == first_amount
        assert persisted.insurance_assessment == first_assessment

        count = await count_calculations(
            db, company.id, secondary.id
        )
        assert count == 1
