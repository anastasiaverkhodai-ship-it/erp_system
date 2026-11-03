"""Reconcile the latest documented severance revision to a financial proposal.

This checks source identity, revision lineage and gross amounts.

It does NOT certify PIT, military levy, employer contribution,
corrected payroll calculation, statutory reporting, posting or payment.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from app.models.payroll_severance_correction_case import (
    PayrollSeveranceCorrectionCase,
)
from app.models.payroll_severance_financial_proposal import (
    PayrollSeveranceFinancialProposal,
)
from app.models.payroll_termination_severance import (
    PayrollTerminationSeverance,
)


class CorrectedSeveranceSourceError(ValueError):
    pass


@dataclass(frozen=True)
class CorrectedSeveranceSourceReconciliation:
    company_id: int
    financial_proposal_id: int
    original_source_id: int
    corrected_source_id: int
    original_revision: int
    corrected_revision: int
    original_gross: Decimal
    corrected_gross: Decimal
    source_chain_verified: bool
    proposed_gross_reconciled: bool
    corrected_statutory_verified: bool = False
    posting_authorized: bool = False


def _positive_amount(value, label):
    if isinstance(value, bool):
        raise CorrectedSeveranceSourceError(
            f"Invalid {label}"
        )

    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CorrectedSeveranceSourceError(
            f"Invalid {label}"
        ) from exc

    if not amount.is_finite() or amount <= 0:
        raise CorrectedSeveranceSourceError(
            f"Invalid {label}"
        )

    if amount.as_tuple().exponent < -2:
        raise CorrectedSeveranceSourceError(
            f"Unsupported precision for {label}"
        )

    return amount


def reconcile_corrected_severance_sources(
    *,
    company_id,
    proposal,
    correction_case,
    sources,
):
    """Verify an unbroken chain from pinned original to the latest source.

    All revisions of the termination event must be supplied. No
    caller-selected revision can be treated as the latest.
    """
    if type(company_id) is not int or company_id <= 0:
        raise CorrectedSeveranceSourceError(
            "Valid company required"
        )

    if (
        proposal is None
        or correction_case is None
        or proposal.company_id != company_id
        or correction_case.company_id != company_id
        or proposal.correction_case_id != correction_case.id
        or proposal.status != "review_required"
        or correction_case.status != "review_required"
    ):
        raise CorrectedSeveranceSourceError(
            "Company-scoped immutable proposal and case required"
        )

    original_id = correction_case.original_severance_source_id

    if type(original_id) is not int or original_id <= 0:
        raise CorrectedSeveranceSourceError(
            "Valid pinned original source required"
        )

    if not isinstance(sources, (list, tuple)) or not sources:
        raise CorrectedSeveranceSourceError(
            "Complete severance revision history required"
        )

    source_by_id = {}
    revisions = set()
    event_id = None

    for source in sources:
        source_id = getattr(source, "id", None)
        revision = getattr(source, "revision", None)
        current_event = getattr(
            source, "termination_event_id", None
        )

        if (
            source.company_id != company_id
            or type(source_id) is not int
            or source_id <= 0
            or type(revision) is not int
            or revision <= 0
            or type(current_event) is not int
            or current_event <= 0
            or source_id in source_by_id
            or revision in revisions
            or source.payroll_input_id
                != correction_case.payroll_input_id
        ):
            raise CorrectedSeveranceSourceError(
                "Severance revisions have conflicting identities"
            )

        if event_id is None:
            event_id = current_event
        elif event_id != current_event:
            raise CorrectedSeveranceSourceError(
                "Severance revisions belong to different events"
            )

        source_by_id[source_id] = source
        revisions.add(revision)

    if original_id not in source_by_id:
        raise CorrectedSeveranceSourceError(
            "Pinned original severance source not in history"
        )

    original = source_by_id[original_id]
    ordered = sorted(
        sources, key=lambda row: row.revision
    )

    if (
        ordered[0].revision != 1
        or len(ordered) != ordered[-1].revision
    ):
        raise CorrectedSeveranceSourceError(
            "Incomplete severance revision history"
        )

    previous = None
    for source in ordered:
        if previous is None:
            if source.supersedes_id is not None:
                raise CorrectedSeveranceSourceError(
                    "Invalid root severance revision"
                )
        elif (
            source.revision != previous.revision + 1
            or source.supersedes_id != previous.id
        ):
            raise CorrectedSeveranceSourceError(
                "Broken severance source revision chain"
            )
        previous = source

    latest = ordered[-1]

    if latest.revision <= original.revision:
        raise CorrectedSeveranceSourceError(
            "No corrected severance source revision"
        )

    original_gross = _positive_amount(
        original.gross_amount,
        "original source gross",
    )
    corrected_gross = _positive_amount(
        latest.gross_amount,
        "corrected source gross",
    )

    if not isinstance(
        proposal.original_financial, dict
    ) or not isinstance(
        proposal.proposed_financial, dict
    ):
        raise CorrectedSeveranceSourceError(
            "Structured financial proposal required"
        )

    proposal_original = _positive_amount(
        proposal.original_financial.get("gross"),
        "proposal original gross",
    )
    proposal_corrected = _positive_amount(
        proposal.proposed_financial.get("gross"),
        "proposal corrected gross",
    )
    pinned_gross = _positive_amount(
        correction_case.original_severance_gross,
        "pinned original gross",
    )

    if (
        original_gross != pinned_gross
        or proposal_original != pinned_gross
        or proposal_corrected != corrected_gross
        or proposal_corrected == proposal_original
    ):
        raise CorrectedSeveranceSourceError(
            "Financial proposal does not match verified source revisions"
        )

    return CorrectedSeveranceSourceReconciliation(
        company_id=company_id,
        financial_proposal_id=proposal.id,
        original_source_id=original.id,
        corrected_source_id=latest.id,
        original_revision=original.revision,
        corrected_revision=latest.revision,
        original_gross=original_gross,
        corrected_gross=corrected_gross,
        source_chain_verified=True,
        proposed_gross_reconciled=True,
    )


async def load_corrected_severance_source_reconciliation(
    db,
    *,
    company_id,
    financial_proposal_id,
):
    """Re-derive source lineage from database rows, not submitted JSON.

    Caller owns the transaction. No inserts or updates occur.
    """
    if (
        type(company_id) is not int
        or company_id <= 0
        or type(financial_proposal_id) is not int
        or financial_proposal_id <= 0
    ):
        raise CorrectedSeveranceSourceError(
            "Valid company and proposal IDs required"
        )

    proposal = await db.scalar(
        select(PayrollSeveranceFinancialProposal).where(
            PayrollSeveranceFinancialProposal.company_id
                == company_id,
            PayrollSeveranceFinancialProposal.id
                == financial_proposal_id,
        )
    )

    if proposal is None:
        raise CorrectedSeveranceSourceError(
            "Financial proposal not found"
        )

    case = await db.scalar(
        select(PayrollSeveranceCorrectionCase).where(
            PayrollSeveranceCorrectionCase.company_id
                == company_id,
            PayrollSeveranceCorrectionCase.id
                == proposal.correction_case_id,
        )
    )

    if case is None:
        raise CorrectedSeveranceSourceError(
            "Severance correction case not found"
        )

    original = await db.scalar(
        select(PayrollTerminationSeverance).where(
            PayrollTerminationSeverance.company_id
                == company_id,
            PayrollTerminationSeverance.id
                == case.original_severance_source_id,
        )
    )

    if original is None:
        raise CorrectedSeveranceSourceError(
            "Pinned original source not found"
        )

    rows = list((
        await db.scalars(
            select(PayrollTerminationSeverance).where(
                PayrollTerminationSeverance.company_id
                    == company_id,
                PayrollTerminationSeverance.termination_event_id
                    == original.termination_event_id,
            ).order_by(
                PayrollTerminationSeverance.revision
            )
        )
    ).all())

    return reconcile_corrected_severance_sources(
        company_id=company_id,
        proposal=proposal,
        correction_case=case,
        sources=rows,
    )
