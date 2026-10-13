"""Payroll correction preflight.

Checks whether an existing payroll calculation is eligible
to enter an explicit correction workflow.

This service does NOT:
- modify attendance or finalized payroll inputs;
- create payroll calculations or accounting entries;
- reverse journals or disbursements;
- commit or roll back the caller's transaction.

All business state must be revalidated when the future
correction creation operation executes.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll import (
    PayrollCalculation,
    PayrollPeriod,
    PayrollPeriodStatus,
)
from app.models.payroll_correction import PayrollCorrection
from app.models.journal_entry import JournalEntry
from app.models.payroll_disbursement import PayrollDisbursement
from app.models.payroll_deduction_result import PayrollDeductionResult

from app.services.payroll_revision_service import (
    require_current_calculation,
)
from app.services.payroll_mutation_guard import (
    serialized_payroll_mutation,
)


class PayrollCorrectionPreflightError(Exception):
    pass


class PayrollCorrectionPreflightNotFound(
    PayrollCorrectionPreflightError
):
    pass


class PayrollCorrectionPreflightConflict(
    PayrollCorrectionPreflightError
):
    pass


@serialized_payroll_mutation(
    PayrollCorrectionPreflightConflict
)
async def prepare_payroll_correction(
    db: AsyncSession,
    *,
    company_id: int,
    original_payroll_calculation_id: int,
    request_key: str,
    reason: str,
) -> dict:
    """Validate correction eligibility under the company lock.

    The result is advisory, not an authorization token.
    Future correction creation MUST repeat the checks
    inside the same transaction as revision insertion.
    """

    if not request_key or not request_key.strip():
        raise PayrollCorrectionPreflightConflict(
            "Correction request_key is required"
        )

    if len(request_key) > 100:
        raise PayrollCorrectionPreflightConflict(
            "Correction request_key exceeds 100 characters"
        )

    if not reason or not reason.strip():
        raise PayrollCorrectionPreflightConflict(
            "Correction reason is required"
        )

    original = await db.scalar(
        select(PayrollCalculation)
        .where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id
            == original_payroll_calculation_id,
        )
        .with_for_update()
    )

    if original is None:
        raise PayrollCorrectionPreflightNotFound(
            "Original payroll calculation not found"
        )

    await require_current_calculation(
        db,
        calculation=original,
        error_type=PayrollCorrectionPreflightConflict,
    )

    period = await db.scalar(
        select(PayrollPeriod).where(
            PayrollPeriod.company_id == company_id,
            PayrollPeriod.id == original.payroll_period_id,
        )
    )

    if period is None:
        raise PayrollCorrectionPreflightConflict(
            "Payroll period not found"
        )

    status = getattr(period.status, "value", period.status)

    if status != PayrollPeriodStatus.FINALIZED.value:
        raise PayrollCorrectionPreflightConflict(
            "Payroll period must be finalized"
        )

    existing_request = await db.scalar(
        select(PayrollCorrection.id).where(
            PayrollCorrection.company_id == company_id,
            PayrollCorrection.request_key == request_key,
        ).limit(1)
    )

    if existing_request is not None:
        raise PayrollCorrectionPreflightConflict(
            "Correction request_key is already used"
        )

    from app.services.payroll_revision_service import (
        current_calculation_filter,
    )

    current = await db.scalar(
        select(PayrollCalculation.id).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.payroll_input_id
            == original.payroll_input_id,
            current_calculation_filter(),
        ).limit(1)
    )

    if current != original.id:
        raise PayrollCorrectionPreflightConflict(
            "Original calculation is not the current revision"
        )

    orphan_revision = await db.scalar(
        select(PayrollCalculation.id).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.payroll_period_id
            == original.payroll_period_id,
            PayrollCalculation.employment_contract_id
            == original.employment_contract_id,
            PayrollCalculation.revision > original.revision,
        ).limit(1)
    )

    if orphan_revision is not None:
        raise PayrollCorrectionPreflightConflict(
            "A later payroll revision already exists"
        )

    active_journal = await db.scalar(
        select(JournalEntry.id).where(
            JournalEntry.company_id == company_id,
            JournalEntry.status != "reversed",
            JournalEntry.reversal_of_id.is_(None),
            (
                (JournalEntry.payroll_calculation_id == original.id)
                |
                JournalEntry.payroll_deduction_result_id.in_(
                    select(PayrollDeductionResult.id).where(
                        PayrollDeductionResult.company_id
                        == company_id,
                        PayrollDeductionResult.payroll_calculation_id
                        == original.id,
                    )
                )
            ),
        ).limit(1)
    )

    if active_journal is not None:
        raise PayrollCorrectionPreflightConflict(
            "Reverse or cancel original payroll journals "
            "before correction"
        )

    active_payout = await db.scalar(
        select(PayrollDisbursement.id).where(
            PayrollDisbursement.company_id == company_id,
            PayrollDisbursement.payroll_calculation_id == original.id,
            PayrollDisbursement.cancelled_at.is_(None),
            PayrollDisbursement.reversed_on.is_(None),
        ).limit(1)
    )

    if active_payout is not None:
        raise PayrollCorrectionPreflightConflict(
            "Reverse or cancel original payroll payouts "
            "before correction"
        )

    return {
        "company_id": company_id,
        "original_payroll_calculation_id": original.id,
        "payroll_period_id": original.payroll_period_id,
        "payroll_input_id": original.payroll_input_id,
        "employment_contract_id": original.employment_contract_id,
        "currency_code": original.currency_code,
        "current_revision": original.revision,
        "next_revision": original.revision + 1,
        "request_key": request_key,
        "reason": reason,
        "eligible": True,
        "requires_verified_recalculation_source": True,
        "automatic_revision_creation_allowed": False,
    }
