"""Controlled GL reversal for a sick-linked payroll calculation.

Does not cancel disbursements, replace sick calculations, or commit.
All database mutations remain in the caller's transaction.
"""
from datetime import date

from sqlalchemy import select

from app.models.journal_entry import JournalEntry, JournalEntryStatus
from app.models.payroll import PayrollCalculationLine
from app.models.payroll_disbursement import PayrollDisbursement
from app.models.payroll_sick_leave import PayrollSickLeaveCalculation
from app.models.payroll_deduction_result import PayrollDeductionResult
from app.services.accounting_reversal import reverse_journal_entry
from app.services.payroll_sick_leave_service import (
    PayrollSickLeaveConflictError,
    PayrollSickLeaveNotFoundError,
)


async def reverse_sick_linked_payroll_journal(
    db,
    *,
    company_id: int,
    original_sick_calculation_id: int,
    payroll_calculation_id: int,
    journal_entry_id: int,
    reversal_date: date,
    reversed_by: int,
):
    if not isinstance(reversal_date, date):
        raise PayrollSickLeaveConflictError(
            "Valid reversal date is required"
        )

    sick = await db.scalar(
        select(PayrollSickLeaveCalculation)
        .where(
            PayrollSickLeaveCalculation.company_id == company_id,
            PayrollSickLeaveCalculation.id
                == original_sick_calculation_id,
        )
        .with_for_update()
    )
    if sick is None:
        raise PayrollSickLeaveNotFoundError(
            "Original sick calculation not found"
        )

    linked_line = await db.scalar(
        select(PayrollCalculationLine.id).where(
            PayrollCalculationLine.company_id == company_id,
            PayrollCalculationLine.payroll_calculation_id
                == payroll_calculation_id,
            PayrollCalculationLine.source_sick_leave_calculation_id
                == original_sick_calculation_id,
        ).limit(1)
    )
    if linked_line is None:
        raise PayrollSickLeaveConflictError(
            "Payroll calculation is not linked to the sick source"
        )

    journal = await db.scalar(
        select(JournalEntry)
        .where(
            JournalEntry.company_id == company_id,
            JournalEntry.id == journal_entry_id,
            JournalEntry.payroll_calculation_id
                == payroll_calculation_id,
            JournalEntry.reversal_of_id.is_(None),
        )
        .with_for_update()
    )

    if journal is None:
        raise PayrollSickLeaveConflictError(
            "Original payroll journal not found"
        )

    if journal.status != JournalEntryStatus.POSTED:
        raise PayrollSickLeaveConflictError(
            "Only a posted original journal can be reversed"
        )

    active_payout = await db.scalar(
        select(PayrollDisbursement.id).where(
            PayrollDisbursement.company_id == company_id,
            PayrollDisbursement.payroll_calculation_id
                == payroll_calculation_id,
            PayrollDisbursement.cancelled_at.is_(None),
            PayrollDisbursement.reversed_on.is_(None),
        ).limit(1)
    )
    if active_payout is not None:
        raise PayrollSickLeaveConflictError(
            "Active payroll payout must be resolved first"
        )

    # Deduction journals may have separate recipient-remittance
    # dependencies. This narrow operation must never bypass them.
    active_deduction_journal = await db.scalar(
        select(JournalEntry.id).where(
            JournalEntry.company_id == company_id,
            JournalEntry.reversal_of_id.is_(None),
            JournalEntry.status == JournalEntryStatus.POSTED,
            JournalEntry.payroll_deduction_result_id.in_(
                select(PayrollDeductionResult.id).where(
                    PayrollDeductionResult.company_id == company_id,
                    PayrollDeductionResult.payroll_calculation_id
                        == payroll_calculation_id,
                )
            ),
        ).limit(1)
    )
    if active_deduction_journal is not None:
        raise PayrollSickLeaveConflictError(
            "Linked deduction journal requires separate reversal"
        )

    # Existing accounting service performs the real journal
    # reversal and enforces its full financial integrity checks.
    reversed_entry = await reverse_journal_entry(
        db,
        company_id=company_id,
        journal_entry_id=journal_entry_id,
        reversal_date=reversal_date,
        reversed_by=reversed_by,
    )

    await db.flush()

    if reversed_entry.reversal_of_id != journal_entry_id:
        raise PayrollSickLeaveConflictError(
            "Accounting reversal did not reference the original"
        )

    if reversed_entry.company_id != company_id:
        raise PayrollSickLeaveConflictError(
            "Accounting reversal company mismatch"
        )

    return reversed_entry
