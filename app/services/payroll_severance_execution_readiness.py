"""Source-bound severance execution readiness.

This module is an explicit fail-closed gate.
It never posts journals, authorizes payments or creates statutory approvals.
"""

from dataclasses import dataclass

from sqlalchemy import select

from app.models.journal_entry import JournalEntry
from app.models.payroll_severance_adjustment_journal import (
    PayrollSeveranceAdjustmentJournal,
)
from app.models.payroll_severance_correction_case import (
    PayrollSeveranceCorrectionCase,
)
from app.models.payroll_severance_financial_proposal import (
    PayrollSeveranceFinancialProposal,
)
from app.models.payroll_severance_independent_review import (
    PayrollSeveranceIndependentReview,
)


class SeveranceExecutionReadinessError(ValueError):
    pass


@dataclass(frozen=True)
class SeveranceExecutionReadiness:
    company_id: int
    financial_proposal_id: int
    original_source_verified: bool
    independent_review_verified: bool
    draft_journal_verified: bool
    corrected_statutory_source_verified: bool
    posting_authorized: bool
    blockers: tuple[str, ...]


def evaluate_severance_execution_readiness(
    *,
    company_id,
    financial_proposal_id,
    proposal,
    correction_case,
    independent_review,
    adjustment_link,
    journal,
    original_source_verified,
) -> SeveranceExecutionReadiness:
    """Evaluate persisted documentary prerequisites without granting posting.

    A corrected financial proposal is not itself a trusted statutory
    calculation. No corrected-source authorization exists in this
    contract, so the result must remain blocked even when the
    original source, reviewer and draft GL journal are valid.
    """
    if (
        type(company_id) is not int
        or company_id <= 0
        or type(financial_proposal_id) is not int
        or financial_proposal_id <= 0
    ):
        raise SeveranceExecutionReadinessError(
            "Valid company and financial proposal required"
        )

    blockers = []

    proposal_ok = (
        proposal is not None
        and proposal.company_id == company_id
        and proposal.id == financial_proposal_id
        and proposal.status == "review_required"
    )

    if not proposal_ok:
        blockers.append("financial_proposal_missing_or_invalid")

    case_ok = (
        proposal_ok
        and correction_case is not None
        and correction_case.company_id == company_id
        and correction_case.id == proposal.correction_case_id
        and correction_case.status == "review_required"
    )

    if not case_ok:
        blockers.append("correction_case_missing_or_invalid")

    review_ok = (
        proposal_ok
        and independent_review is not None
        and independent_review.company_id == company_id
        and independent_review.financial_proposal_id
            == financial_proposal_id
        and independent_review.decision
            == "reviewed_pending_statutory"
        and independent_review.reviewer_id
            != proposal.submitted_by
    )

    if not review_ok:
        blockers.append("independent_review_missing_or_rejected")

    link_ok = (
        proposal_ok
        and adjustment_link is not None
        and adjustment_link.company_id == company_id
        and adjustment_link.financial_proposal_id
            == financial_proposal_id
    )

    journal_ok = (
        link_ok
        and journal is not None
        and journal.company_id == company_id
        and journal.id == adjustment_link.journal_entry_id
        and str(getattr(journal.status, "value", journal.status))
            == "draft"
    )

    if not journal_ok:
        blockers.append("draft_adjustment_journal_missing_or_invalid")

    if not original_source_verified:
        blockers.append("original_trusted_statutory_snapshot_not_verified")

    # This contract does not have a persisted, independently
    # authenticated corrected statutory calculation.
    # Neither a JSON proposal nor a review decision can replace it.
    blockers.append("corrected_trusted_statutory_source_required")

    return SeveranceExecutionReadiness(
        company_id=company_id,
        financial_proposal_id=financial_proposal_id,
        original_source_verified=bool(original_source_verified),
        independent_review_verified=bool(review_ok),
        draft_journal_verified=bool(journal_ok),
        corrected_statutory_source_verified=False,
        posting_authorized=False,
        blockers=tuple(blockers),
    )


async def assess_severance_execution_readiness(
    db,
    *,
    company_id: int,
    financial_proposal_id: int,
) -> SeveranceExecutionReadiness:
    """Read actual company-scoped rows and verify the original legal source.

    Caller owns transaction. This function performs no database writes.
    """
    if (
        type(company_id) is not int
        or company_id <= 0
        or type(financial_proposal_id) is not int
        or financial_proposal_id <= 0
    ):
        raise SeveranceExecutionReadinessError(
            "Valid company and financial proposal required"
        )

    proposal = await db.scalar(
        select(PayrollSeveranceFinancialProposal).where(
            PayrollSeveranceFinancialProposal.company_id
                == company_id,
            PayrollSeveranceFinancialProposal.id
                == financial_proposal_id,
        )
    )

    correction_case = None
    if proposal is not None:
        correction_case = await db.scalar(
            select(PayrollSeveranceCorrectionCase).where(
                PayrollSeveranceCorrectionCase.company_id
                    == company_id,
                PayrollSeveranceCorrectionCase.id
                    == proposal.correction_case_id,
            )
        )

    independent_review = await db.scalar(
        select(PayrollSeveranceIndependentReview).where(
            PayrollSeveranceIndependentReview.company_id
                == company_id,
            PayrollSeveranceIndependentReview.financial_proposal_id
                == financial_proposal_id,
        )
    )

    adjustment_link = await db.scalar(
        select(PayrollSeveranceAdjustmentJournal).where(
            PayrollSeveranceAdjustmentJournal.company_id
                == company_id,
            PayrollSeveranceAdjustmentJournal.financial_proposal_id
                == financial_proposal_id,
        )
    )

    journal = None
    if adjustment_link is not None:
        journal = await db.scalar(
            select(JournalEntry).where(
                JournalEntry.company_id == company_id,
                JournalEntry.id == adjustment_link.journal_entry_id,
            )
        )

    verified_original = False
    if correction_case is not None:
        from app.services.payroll_severance_trusted_classification import (
            require_trusted_severance_snapshot,
        )

        try:
            original_snapshot = await require_trusted_severance_snapshot(
                db,
                company_id=company_id,
                payroll_calculation_id=(
                    correction_case.original_payroll_calculation_id
                ),
            )
            verified_original = original_snapshot is not None
        except ValueError:
            verified_original = False

    return evaluate_severance_execution_readiness(
        company_id=company_id,
        financial_proposal_id=financial_proposal_id,
        proposal=proposal,
        correction_case=correction_case,
        independent_review=independent_review,
        adjustment_link=adjustment_link,
        journal=journal,
        original_source_verified=verified_original,
    )
