"""Real PostgreSQL atomic unlinked sick correction application."""
from datetime import date
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
from app.models.payroll_sick_correction_application import (
    PayrollSickCorrectionApplication,
)
from app.services.payroll_sick_history_service import (
    calculate_sick_from_history,
)
from app.services.payroll_sick_correction_service import (
    request_sick_correction,
)
from app.services.payroll_sick_correction_application_service import (
    apply_unlinked_sick_correction,
)
from app.services.payroll_sick_leave_service import (
    PayrollSickLeaveConflictError,
    get_sick_leave_calculation_for_leave,
)

pytestmark = pytest.mark.asyncio


async def setup_case(db):
    company, primary, secondary, histories, args = await prepare(db)
    original = await calculate_sick_from_history(db, **args)
    evidence = await changed_evidence(db, company, args, reviewed=True)

    correction = await request_sick_correction(
        db,
        company_id=company.id,
        original_calculation_id=original.id,
        insurance_evidence_id=evidence.id,
        request_key=uuid4().hex,
        reason="Approved independently reviewed sick correction",
        requested_by=args["calculated_by"],
    )

    return company, args, original, correction


async def test_atomic_replacement_and_idempotency(employee_engine):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, args, original, correction = await setup_case(db)

        old_amount = original.sick_pay_amount
        old_evidence = original.insurance_evidence_id
        reviewer = args["calculated_by"] + 1

        applied = await apply_unlinked_sick_correction(
            db,
            company_id=company.id,
            correction_id=correction.id,
            correction_date=date(2026, 10, 26),
            reviewed_by=reviewer,
        )

        repeated = await apply_unlinked_sick_correction(
            db,
            company_id=company.id,
            correction_id=correction.id,
            correction_date=date(2026, 10, 26),
            reviewed_by=reviewer,
        )

        assert repeated.id == applied.id

        replacement = await db.get(
            PayrollSickLeaveCalculation,
            applied.replacement_sick_id,
        )

        assert replacement is not None
        assert replacement.supersedes_id == original.id
        assert replacement.revision == original.revision + 1
        assert replacement.insurance_evidence_id == correction.insurance_evidence_id
        assert replacement.sick_pay_amount != old_amount

        assert original.sick_pay_amount == old_amount
        assert original.insurance_evidence_id == old_evidence

        current = await get_sick_leave_calculation_for_leave(
            db,
            company_id=company.id,
            leave_request_id=original.leave_request_id,
        )

        assert current.id == replacement.id

        count = await db.scalar(
            select(func.count())
            .select_from(PayrollSickCorrectionApplication)
            .where(
                PayrollSickCorrectionApplication.company_id
                    == company.id
            )
        )
        assert count == 1


async def test_requester_cannot_approve_own_correction(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, args, original, correction = await setup_case(db)

        with pytest.raises(
            PayrollSickLeaveConflictError,
            match="Requester cannot approve",
        ):
            await apply_unlinked_sick_correction(
                db,
                company_id=company.id,
                correction_id=correction.id,
                correction_date=date(2026, 10, 26),
                reviewed_by=args["calculated_by"],
            )

        count = await db.scalar(
            select(func.count())
            .select_from(PayrollSickCorrectionApplication)
            .where(
                PayrollSickCorrectionApplication.company_id
                    == company.id
            )
        )
        assert count == 0
