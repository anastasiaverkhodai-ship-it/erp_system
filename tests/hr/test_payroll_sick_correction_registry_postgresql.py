"""Real PostgreSQL correction request lifecycle."""
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_secondary_sick_postgresql import prepare

from app.models.payroll_sick_correction import PayrollSickCorrection
from app.models.payroll_sick_leave import PayrollSickLeaveCalculation
from app.services.payroll_sick_history_service import (
    calculate_sick_from_history,
)
from app.services.payroll_sick_correction_service import (
    request_sick_correction,
)
from app.services.payroll_sick_leave_service import (
    PayrollSickLeaveConflictError,
)

pytestmark = pytest.mark.asyncio


async def test_request_idempotent_and_original_unchanged(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, primary, secondary, histories, args = await prepare(db)
        original = await calculate_sick_from_history(db, **args)

        old_id = original.id
        old_amount = original.sick_pay_amount
        old_assessment = original.insurance_assessment

        kwargs = dict(
            company_id=company.id,
            original_calculation_id=old_id,
            insurance_evidence_id=args["insurance_evidence_id"],
            request_key="sick-correction-e2e-one",
            reason="Verified original sick base requires correction",
            requested_by=args["calculated_by"],
        )

        first = await request_sick_correction(db, **kwargs)
        again = await request_sick_correction(db, **kwargs)

        assert first.id == again.id
        assert first.status == "pending_review"
        assert first.original_amount == old_amount

        saved = await db.get(PayrollSickLeaveCalculation, old_id)
        assert saved.sick_pay_amount == old_amount
        assert saved.insurance_assessment == old_assessment

        count = await db.scalar(
            select(func.count())
            .select_from(PayrollSickCorrection)
            .where(PayrollSickCorrection.company_id == company.id)
        )
        assert count == 1


async def test_conflicting_request_refused_without_mutation(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, primary, secondary, histories, args = await prepare(db)
        original = await calculate_sick_from_history(db, **args)

        base = dict(
            company_id=company.id,
            original_calculation_id=original.id,
            insurance_evidence_id=args["insurance_evidence_id"],
            request_key="sick-correction-e2e-conflict",
            reason="Verified insurance history needs review",
            requested_by=args["calculated_by"],
        )

        first = await request_sick_correction(db, **base)

        with pytest.raises(PayrollSickLeaveConflictError):
            await request_sick_correction(
                db,
                **{
                    **base,
                    "reason": "Different revised statutory evidence",
                },
            )

        assert first.status == "pending_review"

        count = await db.scalar(
            select(func.count())
            .select_from(PayrollSickCorrection)
            .where(
                PayrollSickCorrection.company_id == company.id
            )
        )
        assert count == 1
