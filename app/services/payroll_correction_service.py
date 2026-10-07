from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll import PayrollCalculation
from app.models.payroll_correction import PayrollCorrection
from app.services.payroll_mutation_guard import serialized_payroll_mutation


class PayrollCorrectionError(Exception):
    pass


class PayrollCorrectionNotFoundError(PayrollCorrectionError):
    pass


class PayrollCorrectionSourceStateError(PayrollCorrectionError):
    pass


@serialized_payroll_mutation(PayrollCorrectionSourceStateError)
async def create_payroll_correction_link(
    db: AsyncSession,
    *,
    company_id: int,
    original_payroll_calculation_id: int,
    replacement_payroll_calculation_id: int,
    request_key: str,
    reason: str,
    created_by: int,
) -> PayrollCorrection:
    existing = await db.scalar(
        select(PayrollCorrection).where(
            PayrollCorrection.company_id == company_id,
            PayrollCorrection.request_key == request_key,
        )
    )

    if existing is not None:
        if (
            existing.original_payroll_calculation_id
            != original_payroll_calculation_id
            or existing.replacement_payroll_calculation_id
            != replacement_payroll_calculation_id
            or existing.reason != reason
        ):
            raise PayrollCorrectionSourceStateError(
                "Request key was used for different payroll correction data"
            )
        return existing

    original = await db.scalar(
        select(PayrollCalculation)
        .where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id == original_payroll_calculation_id,
        )
        .with_for_update()
    )

    if original is None:
        raise PayrollCorrectionNotFoundError(
            "Original payroll calculation not found"
        )

    replacement = await db.scalar(
        select(PayrollCalculation)
        .where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id == replacement_payroll_calculation_id,
        )
        .with_for_update()
    )

    if replacement is None:
        raise PayrollCorrectionNotFoundError(
            "Replacement payroll calculation not found"
        )

    if original.id == replacement.id:
        raise PayrollCorrectionSourceStateError(
            "Original and replacement payroll calculations must differ"
        )

    if original.payroll_period_id != replacement.payroll_period_id:
        raise PayrollCorrectionSourceStateError(
            "Correction calculations must belong to the same payroll period"
        )

    if (
        original.employment_contract_id
        != replacement.employment_contract_id
    ):
        raise PayrollCorrectionSourceStateError(
            "Correction calculations must belong to the same employment contract"
        )

    if replacement.revision != original.revision + 1:
        raise PayrollCorrectionSourceStateError(
            "Replacement payroll calculation must be the next revision"
        )

    if original.payroll_input_id != replacement.payroll_input_id or original.currency_code != replacement.currency_code:
        raise PayrollCorrectionSourceStateError('Correction must preserve payroll input and currency')
    from app.services.payroll_revision_service import require_current_calculation
    await require_current_calculation(db, calculation=original, error_type=PayrollCorrectionSourceStateError)
    from app.models.journal_entry import JournalEntry
    from app.models.payroll_disbursement import PayrollDisbursement
    from app.models.payroll_deduction_result import PayrollDeductionResult
    active_journal = await db.scalar(select(JournalEntry.id).where(
        JournalEntry.company_id == company_id, JournalEntry.status != 'reversed',
        JournalEntry.reversal_of_id.is_(None),
        (JournalEntry.payroll_calculation_id == original.id) |
        JournalEntry.payroll_deduction_result_id.in_(select(PayrollDeductionResult.id).where(
            PayrollDeductionResult.company_id == company_id,
            PayrollDeductionResult.payroll_calculation_id == original.id))).limit(1))
    active_payout = await db.scalar(select(PayrollDisbursement.id).where(
        PayrollDisbursement.company_id == company_id,
        PayrollDisbursement.payroll_calculation_id == original.id,
        PayrollDisbursement.cancelled_at.is_(None), PayrollDisbursement.reversed_on.is_(None)).limit(1))
    if active_journal is not None or active_payout is not None:
        raise PayrollCorrectionSourceStateError('Reverse or cancel original postings and payouts before linking correction')

    replacement_used = await db.scalar(
        select(PayrollCorrection.id).where(
            PayrollCorrection.company_id == company_id,
            PayrollCorrection.replacement_payroll_calculation_id
            == replacement_payroll_calculation_id,
        )
    )

    if replacement_used is not None:
        raise PayrollCorrectionSourceStateError(
            "Replacement payroll calculation is already linked to a correction"
        )

    correction = PayrollCorrection(
        company_id=company_id,
        original_payroll_calculation_id=original_payroll_calculation_id,
        replacement_payroll_calculation_id=replacement_payroll_calculation_id,
        request_key=request_key,
        reason=reason,
        created_by=created_by,
    )

    db.add(correction)
    await db.flush()
    return correction


async def get_payroll_correction(
    db: AsyncSession,
    *,
    company_id: int,
    correction_id: int,
) -> PayrollCorrection:
    correction = await db.scalar(
        select(PayrollCorrection).where(
            PayrollCorrection.company_id == company_id,
            PayrollCorrection.id == correction_id,
        )
    )

    if correction is None:
        raise PayrollCorrectionNotFoundError(
            "Payroll correction not found"
        )

    return correction
