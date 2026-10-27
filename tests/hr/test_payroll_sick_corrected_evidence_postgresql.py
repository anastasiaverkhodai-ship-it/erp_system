"""Real PostgreSQL registration of independently reviewed correction evidence."""
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_secondary_sick_postgresql import prepare
from test_payroll_sick_correction_preview_postgresql import (
    changed_evidence,
)

from app.models.payroll_sick_leave import PayrollSickLeaveCalculation
from app.models.payroll_sick_correction import PayrollSickCorrection
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


async def test_corrected_verified_evidence_can_be_registered(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, primary, secondary, histories, args = (
            await prepare(db)
        )

        original = await calculate_sick_from_history(db, **args)
        old_amount = original.sick_pay_amount
        old_evidence = original.insurance_evidence_id

        corrected = await changed_evidence(
            db, company, args, reviewed=True
        )

        request = await request_sick_correction(
            db,
            company_id=company.id,
            original_calculation_id=original.id,
            insurance_evidence_id=corrected.id,
            request_key=uuid4().hex,
            reason="Independently reviewed corrected insurance history",
            requested_by=args["calculated_by"],
        )

        assert request.status == "pending_review"
        assert request.original_calculation_id == original.id
        assert request.insurance_evidence_id == corrected.id
        assert request.original_amount == old_amount

        saved = await db.get(
            PayrollSickLeaveCalculation, original.id
        )
        assert saved.insurance_evidence_id == old_evidence
        assert saved.sick_pay_amount == old_amount

        count = await db.scalar(
            select(func.count())
            .select_from(PayrollSickLeaveCalculation)
            .where(
                PayrollSickLeaveCalculation.company_id == company.id,
                PayrollSickLeaveCalculation.leave_request_id
                    == original.leave_request_id,
            )
        )
        assert count == 1


async def test_unreviewed_corrected_evidence_cannot_register(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, primary, secondary, histories, args = (
            await prepare(db)
        )

        original = await calculate_sick_from_history(db, **args)

        corrected = await changed_evidence(
            db, company, args, reviewed=False
        )

        with pytest.raises(
            PayrollSickLeaveConflictError,
            match="Verified insurance evidence is required",
        ):
            await request_sick_correction(
                db,
                company_id=company.id,
                original_calculation_id=original.id,
                insurance_evidence_id=corrected.id,
                request_key=uuid4().hex,
                reason="Correction using unreviewed insurance history",
                requested_by=args["calculated_by"],
            )

        count = await db.scalar(
            select(func.count())
            .select_from(PayrollSickCorrection)
            .where(
                PayrollSickCorrection.company_id == company.id,
                PayrollSickCorrection.original_calculation_id
                    == original.id,
            )
        )
        assert count == 0
