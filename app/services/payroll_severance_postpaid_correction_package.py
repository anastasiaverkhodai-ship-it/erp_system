"""Source-bound post-payment severance correction package.

Combines:
- immutable correction case and financial proposal;
- latest verified severance source revision;
- independently reconciled gross delta;
- separately identified PIT, levy, contribution and net deltas;
- existing financial execution readiness.

The package is read-only and fail-closed.

Financial proposals do not independently certify statutory taxes.
This service NEVER posts journals, initiates refunds or payments,
or supersedes previously posted payroll calculations.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from app.models.payroll_severance_correction_case import (
    PayrollSeveranceCorrectionCase,
)
from app.models.payroll_severance_financial_proposal import (
    PayrollSeveranceFinancialProposal,
)

from app.services.payroll_severance_corrected_source_reconciliation import (
    CorrectedSeveranceSourceError,
    load_corrected_severance_source_reconciliation,
)
from app.services.payroll_severance_financial_delta_ledger import (
    SeveranceDeltaError,
    calculate_severance_deltas,
)
from app.services.payroll_severance_execution_readiness import (
    assess_severance_execution_readiness,
)


class SeverancePostpaidPackageError(ValueError):
    """Invalid or incomplete financial correction evidence."""


ZERO = Decimal("0.00")


@dataclass(frozen=True)
class SeverancePostpaidCorrectionPackage:
    company_id: int
    correction_case_id: int
    financial_proposal_id: int

    original_payroll_calculation_id: int
    original_severance_source_id: int
    corrected_severance_source_id: int

    original_source_revision: int
    corrected_source_revision: int

    original_gross: Decimal
    corrected_gross: Decimal

    gross_delta: Decimal
    personal_income_tax_delta: Decimal
    military_levy_delta: Decimal
    employer_contribution_delta: Decimal
    net_delta: Decimal

    settlement_direction: str
    settlement_amount: Decimal

    source_lineage_verified: bool
    financial_delta_reconciled: bool

    statutory_tax_verified: bool
    posting_authorized: bool
    payment_authorized: bool

    blockers: tuple[str, ...]


def _amount(value, label):
    """Require finite, two-decimal monetary values."""

    if type(value) is not Decimal:
        raise SeverancePostpaidPackageError(
            f"{label} must be Decimal"
        )

    if not value.is_finite():
        raise SeverancePostpaidPackageError(
            f"Nonfinite {label}"
        )

    if value.as_tuple().exponent < -2:
        raise SeverancePostpaidPackageError(
            f"Unsupported precision for {label}"
        )

    return value


def _id(value, label):
    if type(value) is not int or value <= 0:
        raise SeverancePostpaidPackageError(
            f"Invalid {label}"
        )
    return value


def _settlement_direction(net_delta):
    """Indicate direction, not authorization to move money."""

    if net_delta > ZERO:
        return "additional_payment", net_delta

    if net_delta < ZERO:
        return "overpayment_recovery", -net_delta

    return "no_net_settlement", ZERO


def build_severance_postpaid_correction_package(
    *,
    company_id,
    case,
    proposal,
    lineage,
    deltas,
    readiness,
):
    """Construct a complete, non-executable correction package.

    Every input is revalidated. In particular, proposal tax amounts
    remain unverified even if an independent review is recorded.
    """

    _id(company_id, "company_id")

    if case is None or proposal is None:
        raise SeverancePostpaidPackageError(
            "Correction case and proposal required"
        )

    _id(case.id, "correction_case_id")
    _id(proposal.id, "financial_proposal_id")

    if (
        case.company_id != company_id
        or proposal.company_id != company_id
        or case.status != "review_required"
        or proposal.status != "review_required"
        or proposal.correction_case_id != case.id
    ):
        raise SeverancePostpaidPackageError(
            "Company-scoped immutable correction identity mismatch"
        )

    if lineage is None or deltas is None or readiness is None:
        raise SeverancePostpaidPackageError(
            "Independent source, delta and readiness evidence required"
        )

    if (
        lineage.company_id != company_id
        or lineage.financial_proposal_id != proposal.id
        or lineage.original_source_id
            != case.original_severance_source_id
        or lineage.original_source_id
            != deltas.original_source_id
        or lineage.corrected_source_id
            == lineage.original_source_id
        or lineage.source_chain_verified is not True
        or lineage.proposed_gross_reconciled is not True
        or lineage.posting_authorized is not False
    ):
        raise SeverancePostpaidPackageError(
            "Corrected severance source is not independently reconciled"
        )

    if (
        deltas.company_id != company_id
        or deltas.case_id != case.id
        or deltas.proposal_id != proposal.id
        or deltas.original_calculation_id
            != case.original_payroll_calculation_id
        or deltas.reconciled is not True
    ):
        raise SeverancePostpaidPackageError(
            "Financial delta provenance mismatch"
        )

    if (
        readiness.company_id != company_id
        or readiness.financial_proposal_id != proposal.id
        or readiness.posting_authorized is not False
    ):
        raise SeverancePostpaidPackageError(
            "Execution readiness identity or safety mismatch"
        )

    if (
        readiness.corrected_source_chain_verified is not True
        or readiness.proposed_gross_reconciled is not True
    ):
        raise SeverancePostpaidPackageError(
            "Database-backed readiness has not verified source revision"
        )

    if (
        type(lineage.original_revision) is not int
        or type(lineage.corrected_revision) is not int
        or lineage.original_revision < 1
        or lineage.corrected_revision
            <= lineage.original_revision
    ):
        raise SeverancePostpaidPackageError(
            "Corrected source must follow the original revision"
        )

    original_gross = _amount(
        deltas.original_gross,
        "original_gross",
    )
    corrected_gross = _amount(
        deltas.corrected_gross,
        "corrected_gross",
    )
    gross_delta = _amount(
        deltas.gross_delta,
        "gross_delta",
    )
    pit_delta = _amount(
        deltas.personal_income_tax_delta,
        "personal_income_tax_delta",
    )
    levy_delta = _amount(
        deltas.military_levy_delta,
        "military_levy_delta",
    )
    contribution_delta = _amount(
        deltas.employer_contribution_delta,
        "employer_contribution_delta",
    )
    net_delta = _amount(
        deltas.net_delta,
        "net_delta",
    )

    if (
        original_gross <= ZERO
        or corrected_gross <= ZERO
        or original_gross != lineage.original_gross
        or corrected_gross != lineage.corrected_gross
        or corrected_gross - original_gross != gross_delta
        or gross_delta - pit_delta - levy_delta != net_delta
        or not any((
            gross_delta,
            pit_delta,
            levy_delta,
            contribution_delta,
            net_delta,
        ))
    ):
        raise SeverancePostpaidPackageError(
            "Post-payment severance amounts do not reconcile"
        )

    direction, settlement_amount = _settlement_direction(
        net_delta
    )

    blockers = tuple(getattr(readiness, "blockers", ()))

    if not all(
        isinstance(item, str) and bool(item.strip())
        for item in blockers
    ):
        raise SeverancePostpaidPackageError(
            "Invalid execution readiness blockers"
        )

    # Tax figures in the proposal must not turn into approval
    # merely because arithmetic and gross source lineage match.
    required_blockers = (
        "corrected_trusted_statutory_source_required",
        "postpaid_financial_settlement_approval_required",
    )

    blockers = tuple(dict.fromkeys(
        (*blockers, *required_blockers)
    ))

    return SeverancePostpaidCorrectionPackage(
        company_id=company_id,
        correction_case_id=case.id,
        financial_proposal_id=proposal.id,
        original_payroll_calculation_id=(
            case.original_payroll_calculation_id
        ),
        original_severance_source_id=(
            lineage.original_source_id
        ),
        corrected_severance_source_id=(
            lineage.corrected_source_id
        ),
        original_source_revision=(
            lineage.original_revision
        ),
        corrected_source_revision=(
            lineage.corrected_revision
        ),
        original_gross=original_gross,
        corrected_gross=corrected_gross,
        gross_delta=gross_delta,
        personal_income_tax_delta=pit_delta,
        military_levy_delta=levy_delta,
        employer_contribution_delta=contribution_delta,
        net_delta=net_delta,
        settlement_direction=direction,
        settlement_amount=settlement_amount,
        source_lineage_verified=True,
        financial_delta_reconciled=True,
        statutory_tax_verified=False,
        posting_authorized=False,
        payment_authorized=False,
        blockers=blockers,
    )


async def load_severance_postpaid_correction_package(
    db,
    *,
    company_id,
    financial_proposal_id,
):
    """Load all evidence from PostgreSQL and build the package.

    No caller-provided financial figures or review flags are accepted.
    """

    _id(company_id, "company_id")
    _id(financial_proposal_id, "financial_proposal_id")

    proposal = await db.scalar(
        select(PayrollSeveranceFinancialProposal).where(
            PayrollSeveranceFinancialProposal.company_id
                == company_id,
            PayrollSeveranceFinancialProposal.id
                == financial_proposal_id,
        )
    )

    if proposal is None:
        raise SeverancePostpaidPackageError(
            "Company-scoped financial proposal not found"
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
        raise SeverancePostpaidPackageError(
            "Company-scoped severance case not found"
        )

    try:
        lineage = (
            await load_corrected_severance_source_reconciliation(
                db,
                company_id=company_id,
                financial_proposal_id=financial_proposal_id,
            )
        )

        deltas = calculate_severance_deltas(
            case=case,
            proposal=proposal,
        )

    except (
        CorrectedSeveranceSourceError,
        SeveranceDeltaError,
    ) as exc:
        raise SeverancePostpaidPackageError(
            "Verified severance source and financial delta required"
        ) from exc

    readiness = await assess_severance_execution_readiness(
        db,
        company_id=company_id,
        financial_proposal_id=financial_proposal_id,
    )

    return build_severance_postpaid_correction_package(
        company_id=company_id,
        case=case,
        proposal=proposal,
        lineage=lineage,
        deltas=deltas,
        readiness=readiness,
    )
