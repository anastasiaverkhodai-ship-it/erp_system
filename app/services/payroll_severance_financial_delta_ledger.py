"""Severance financial correction deltas bound to immutable DB sources.

A delta is not a journal instruction or a legal tax determination.
Never derive severance-only employee payments from whole-payroll
disbursements or whole-payroll tax remittances.
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
from app.services.payroll_severance_financial_approval_gate import (
    snapshot_from_json,
)


class SeveranceDeltaError(ValueError):
    pass


@dataclass(frozen=True)
class SeveranceFinancialDeltas:
    company_id: int
    case_id: int
    proposal_id: int
    original_source_id: int
    original_calculation_id: int

    original_gross: Decimal
    corrected_gross: Decimal

    gross_delta: Decimal
    personal_income_tax_delta: Decimal
    military_levy_delta: Decimal
    employer_contribution_delta: Decimal
    net_delta: Decimal

    reconciled: bool

    # Financial amounts proposed by a user are NOT approved tax facts.
    statutory_source_verified: bool = False
    approval_persisted: bool = False
    employee_payment_authorized: bool = False
    employee_recovery_authorized: bool = False
    tax_refund_authorized: bool = False
    journal_posting_authorized: bool = False


def calculate_severance_deltas(*, case, proposal):
    if (
        case is None
        or proposal is None
        or type(getattr(case, "id", None)) is not int
        or type(getattr(proposal, "id", None)) is not int
        or case.id <= 0
        or proposal.id <= 0
        or getattr(case, "status", None) != "review_required"
        or getattr(proposal, "status", None) != "review_required"
        or case.company_id != proposal.company_id
        or case.id != proposal.correction_case_id
    ):
        raise SeveranceDeltaError(
            "Immutable case and proposal identity mismatch"
        )

    try:
        original = snapshot_from_json(
            proposal.original_financial
        )
        corrected = snapshot_from_json(
            proposal.proposed_financial
        )
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise SeveranceDeltaError(
            "Invalid immutable financial evidence"
        ) from exc

    if original.gross != case.original_severance_gross:
        raise SeveranceDeltaError(
            "Original gross differs from pinned severance source"
        )

    delta = {
        field: getattr(corrected, field) - getattr(original, field)
        for field in (
            "gross",
            "personal_income_tax",
            "military_levy",
            "employer_contribution",
            "net",
        )
    }

    if not any(value != 0 for value in delta.values()):
        raise SeveranceDeltaError(
            "No correction difference exists"
        )

    if delta["net"] != (
        delta["gross"]
        - delta["personal_income_tax"]
        - delta["military_levy"]
    ):
        raise SeveranceDeltaError(
            "Gross, tax and net correction deltas differ"
        )

    return SeveranceFinancialDeltas(
        company_id=case.company_id,
        case_id=case.id,
        proposal_id=proposal.id,
        original_source_id=case.original_severance_source_id,
        original_calculation_id=(
            case.original_payroll_calculation_id
        ),
        original_gross=original.gross,
        corrected_gross=corrected.gross,
        gross_delta=delta["gross"],
        personal_income_tax_delta=delta["personal_income_tax"],
        military_levy_delta=delta["military_levy"],
        employer_contribution_delta=(
            delta["employer_contribution"]
        ),
        net_delta=delta["net"],
        reconciled=True,
    )


async def load_severance_financial_deltas(
    db,
    *,
    company_id,
    correction_case_id,
):
    if (
        type(company_id) is not int
        or company_id <= 0
        or type(correction_case_id) is not int
        or correction_case_id <= 0
    ):
        raise SeveranceDeltaError(
            "Valid company and correction case IDs required"
        )

    case = await db.scalar(
        select(PayrollSeveranceCorrectionCase).where(
            PayrollSeveranceCorrectionCase.company_id
            == company_id,
            PayrollSeveranceCorrectionCase.id
            == correction_case_id,
        )
    )

    if case is None:
        raise SeveranceDeltaError(
            "Correction case not found"
        )

    proposals = list((
        await db.scalars(
            select(PayrollSeveranceFinancialProposal).where(
                PayrollSeveranceFinancialProposal.company_id
                == company_id,
                PayrollSeveranceFinancialProposal.correction_case_id
                == correction_case_id,
            )
        )
    ).all())

    if len(proposals) != 1:
        raise SeveranceDeltaError(
            "Exactly one immutable financial proposal required"
        )

    return calculate_severance_deltas(
        case=case,
        proposal=proposals[0],
    )
