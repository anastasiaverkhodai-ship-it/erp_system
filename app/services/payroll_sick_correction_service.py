"""Register sick corrections without mutating paid calculations."""
from decimal import Decimal
from sqlalchemy import select

from app.models.payroll_sick_leave import PayrollSickLeaveCalculation
from app.models.payroll_sick_correction import PayrollSickCorrection
from app.services.payroll_insurance_evidence_service import (
    get_insurance_evidence,
)
from app.services.payroll_sick_leave_service import (
    PayrollSickLeaveConflictError,
    PayrollSickLeaveNotFoundError,
)
from app.services.payroll_mutation_guard import (
    serialized_payroll_mutation,
)


@serialized_payroll_mutation(PayrollSickLeaveConflictError)
async def request_sick_correction(
    db, *, company_id, original_calculation_id,
    insurance_evidence_id, request_key, reason, requested_by,
):
    if (
        not isinstance(request_key, str)
        or not request_key.strip()
        or len(request_key) > 100
    ):
        raise PayrollSickLeaveConflictError(
            "A valid correction request key is required"
        )

    if (
        not isinstance(reason, str)
        or len(reason.strip()) < 10
    ):
        raise PayrollSickLeaveConflictError(
            "Documented correction reason is required"
        )

    existing = await db.scalar(
        select(PayrollSickCorrection).where(
            PayrollSickCorrection.company_id == company_id,
            PayrollSickCorrection.request_key == request_key,
        )
    )

    if existing is not None:
        if (
            existing.original_calculation_id
                != original_calculation_id
            or existing.insurance_evidence_id
                != insurance_evidence_id
            or existing.reason != reason.strip()
            or existing.requested_by != requested_by
        ):
            raise PayrollSickLeaveConflictError(
                "Correction request key was used with different details"
            )
        return existing

    original = await db.scalar(
        select(PayrollSickLeaveCalculation)
        .where(
            PayrollSickLeaveCalculation.company_id == company_id,
            PayrollSickLeaveCalculation.id
                == original_calculation_id,
        )
        .with_for_update()
    )

    if original is None:
        raise PayrollSickLeaveNotFoundError(
            "Original sick calculation not found"
        )

    # Registration is append-only and does not alter payroll or payments.
    # Financial replacement is performed by the atomic application service.
    if (
        original.insurance_evidence_id is None
        or original.insurance_assessment is None
    ):
        raise PayrollSickLeaveConflictError(
            "Only evidence-backed sick calculations can be corrected"
        )

    evidence = await get_insurance_evidence(
        db,
        company_id=company_id,
        evidence_id=insurance_evidence_id,
        lock=True,
    )

    if evidence.status != "verified":
        raise PayrollSickLeaveConflictError(
            "Verified insurance evidence is required"
        )

    original_evidence = await get_insurance_evidence(db, company_id=company_id,
        evidence_id=original.insurance_evidence_id, lock=True)
    if (original_evidence.employee_id != evidence.employee_id
            or original_evidence.case_start != evidence.case_start):
        raise PayrollSickLeaveConflictError('Correction evidence must identify the same employee and insurance case')
    from app.services.payroll_sick_revision_service import current_sick_calculation_filter
    if await db.scalar(select(PayrollSickLeaveCalculation.id).where(
            PayrollSickLeaveCalculation.company_id==company_id,
            PayrollSickLeaveCalculation.id==original.id, current_sick_calculation_filter())) is None:
        raise PayrollSickLeaveConflictError('Correction must identify the current sick calculation')

    already_requested = await db.scalar(
        select(PayrollSickCorrection)
        .where(
            PayrollSickCorrection.company_id == company_id,
            PayrollSickCorrection.original_calculation_id
                == original_calculation_id,
        )
    )

    if already_requested is not None:
        raise PayrollSickLeaveConflictError(
            "An existing correction request must be resolved first"
        )

    row = PayrollSickCorrection(
        company_id=company_id,
        original_calculation_id=original_calculation_id,
        insurance_evidence_id=insurance_evidence_id,
        request_key=request_key,
        reason=reason.strip(),
        original_amount=Decimal(original.sick_pay_amount),
        status="pending_review",
        requested_by=requested_by,
    )

    db.add(row)
    await db.flush()
    return row
