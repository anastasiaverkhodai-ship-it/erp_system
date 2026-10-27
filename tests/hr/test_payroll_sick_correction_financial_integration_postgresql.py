"""Real PostgreSQL integration of correction financial preflight."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_secondary_sick_postgresql import prepare

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


async def test_posted_payroll_can_register_request_without_destructive_replacement(employee_engine):
    from test_payroll_sick_application_postgresql import setup
    from test_payroll_sick_correction_preview_postgresql import changed_evidence
    from app.services.payroll_sick_correction_financial_guard import require_sick_replacement_financially_clear
    from app.models.payroll_sick_leave import PayrollSickLeaveCalculation
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,args,original,_,_,_,journal=await setup(db)
        evidence=await changed_evidence(db,company,args)
        count_before=await db.scalar(select(func.count()).select_from(PayrollSickLeaveCalculation))
        correction=await request_sick_correction(db,company_id=company.id,
            original_calculation_id=original.id,insurance_evidence_id=evidence.id,
            request_key='posted-request',reason='Reviewed insurance history requires correction',
            requested_by=args['calculated_by'])
        assert correction.status=='pending_review'
        assert journal.status=='posted'
        assert await db.scalar(select(func.count()).select_from(PayrollSickLeaveCalculation))==count_before
        with pytest.raises(PayrollSickLeaveConflictError,match='full financial sick correction'):
            await require_sick_replacement_financially_clear(db,company_id=company.id,
                original_calculation_id=original.id)


async def test_unlinked_calculation_can_register_correction(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, primary, secondary, histories, args = (
            await prepare(db)
        )
        original = await calculate_sick_from_history(db, **args)

        correction = await request_sick_correction(
            db,
            company_id=company.id,
            original_calculation_id=original.id,
            insurance_evidence_id=args["insurance_evidence_id"],
            request_key="allowed-unlinked-correction",
            reason="Verified sick calculation requires correction",
            requested_by=args["calculated_by"],
        )

        assert correction.status == "pending_review"
        assert correction.original_calculation_id == original.id
        assert correction.original_amount == original.sick_pay_amount
