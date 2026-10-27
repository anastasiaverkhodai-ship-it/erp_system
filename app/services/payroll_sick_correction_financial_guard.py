"""Fail-closed financial preflight for sick calculation replacement."""
from sqlalchemy import select

from app.models.payroll import PayrollCalculationLine
from app.models.journal_entry import JournalEntry
from app.models.payroll_disbursement import PayrollDisbursement
from app.models.payroll_sick_leave import PayrollSickLeaveCalculation
from app.services.payroll_sick_leave_service import (
    PayrollSickLeaveConflictError,
    PayrollSickLeaveNotFoundError,
)


async def require_sick_replacement_financially_clear(
    db, *, company_id, original_calculation_id,
):
    original = await db.scalar(
        select(PayrollSickLeaveCalculation)
        .where(
            PayrollSickLeaveCalculation.company_id == company_id,
            PayrollSickLeaveCalculation.id == original_calculation_id,
        )
        .with_for_update()
    )

    if original is None:
        raise PayrollSickLeaveNotFoundError(
            "Original sick calculation not found"
        )

    linked = list((await db.scalars(
        select(PayrollCalculationLine.payroll_calculation_id)
        .where(
            PayrollCalculationLine.company_id == company_id,
            PayrollCalculationLine.source_sick_leave_calculation_id
                == original_calculation_id,
        )
        .distinct()
    )).all())

    if linked:
        active_journal = await db.scalar(
            select(JournalEntry.id)
            .where(
                JournalEntry.company_id == company_id,
                JournalEntry.payroll_calculation_id.in_(linked),
                JournalEntry.status != "reversed",
                JournalEntry.reversal_of_id.is_(None),
            )
            .limit(1)
        )

        if active_journal is not None:
            raise PayrollSickLeaveConflictError(
                "Original active payroll journal must be preserved; "
                "use the full financial sick correction application"
            )

        active_payment = await db.scalar(
            select(PayrollDisbursement.id)
            .where(
                PayrollDisbursement.company_id == company_id,
                PayrollDisbursement.payroll_calculation_id.in_(linked),
                PayrollDisbursement.cancelled_at.is_(None),
                PayrollDisbursement.reversed_on.is_(None),
            )
            .limit(1)
        )

        if active_payment is not None:
            raise PayrollSickLeaveConflictError(
                "Original payroll payout must be preserved; "
                "use the full financial sick correction application"
            )

        # A payroll line may still be linked to the old immutable
        # sick source. That line must be replaced by an explicitly
        # versioned payroll correction, not silently rewritten.
        raise PayrollSickLeaveConflictError(
            "Sick source is linked to payroll; use reviewed "
            "payroll revision and correction workflow"
        )

    return original
