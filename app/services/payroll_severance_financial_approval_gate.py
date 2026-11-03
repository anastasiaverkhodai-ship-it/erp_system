"""Source-integrity preflight for independently reviewed severance proposals.

This service verifies immutable financial evidence and reviewer separation.
It does NOT validate Ukrainian statutory source applicability or grant
permission to post journals, recover wages, refund tax or send payments.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.services.payroll_severance_net_tax_settlement import (
    SeveranceFinancialSnapshot,
    amount,
)


class SeveranceApprovalGateError(ValueError):
    pass


FIELDS = (
    "gross",
    "personal_income_tax",
    "military_levy",
    "net",
    "employer_contribution",
)


@dataclass(frozen=True)
class FinancialReviewEvidence:
    reviewer_id: int
    submitter_id: int
    document_reference: str
    normative_source_reference: str
    reviewed_original: SeveranceFinancialSnapshot
    reviewed_corrected: SeveranceFinancialSnapshot

    # These flags intentionally cannot be elevated by this service.
    statutory_source_verified: bool = False
    journal_posting_authorized: bool = False
    employee_recovery_authorized: bool = False
    tax_refund_authorized: bool = False


@dataclass(frozen=True)
class SeveranceApprovalPreflight:
    original: SeveranceFinancialSnapshot
    corrected: SeveranceFinancialSnapshot
    gross_delta: Decimal
    net_delta: Decimal
    reviewer_separated: bool
    financial_integrity_reconciled: bool
    statutory_source_verified: bool = False
    journal_posting_authorized: bool = False
    employee_recovery_authorized: bool = False
    tax_refund_authorized: bool = False


def snapshot_from_json(raw):
    if not isinstance(raw, dict) or set(raw) != set(FIELDS):
        raise SeveranceApprovalGateError(
            "Complete immutable financial snapshot required"
        )

    try:
        values = {
            key: amount(
                raw[key],
                key,
                nonnegative=True,
            )
            for key in FIELDS
        }
        snapshot = SeveranceFinancialSnapshot(**values)
        snapshot.validate()
    except (ValueError, ArithmeticError, TypeError) as exc:
        raise SeveranceApprovalGateError(
            "Invalid stored financial snapshot"
        ) from exc

    return snapshot


def require_exact_snapshot(actual, expected):
    if not isinstance(expected, SeveranceFinancialSnapshot):
        raise SeveranceApprovalGateError(
            "Typed independently reviewed snapshot required"
        )

    try:
        values = expected.validate()
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise SeveranceApprovalGateError(
            "Invalid independent review values"
        ) from exc

    if any(
        getattr(actual, field) != values[field]
        for field in FIELDS
    ):
        raise SeveranceApprovalGateError(
            "Reviewed amounts differ from immutable proposal"
        )


def financial_approval_preflight(
    *,
    proposal,
    case,
    evidence,
):
    if not isinstance(evidence, FinancialReviewEvidence):
        raise SeveranceApprovalGateError(
            "Typed independent review evidence required"
        )

    if (
        type(evidence.reviewer_id) is not int
        or evidence.reviewer_id <= 0
        or type(evidence.submitter_id) is not int
        or evidence.submitter_id <= 0
        or evidence.reviewer_id == evidence.submitter_id
    ):
        raise SeveranceApprovalGateError(
            "Independent reviewer must differ from submitter"
        )

    if (
        not isinstance(evidence.document_reference, str)
        or len(evidence.document_reference.strip()) < 10
        or not isinstance(evidence.normative_source_reference, str)
        or len(evidence.normative_source_reference.strip()) < 10
    ):
        raise SeveranceApprovalGateError(
            "Review and normative source references required"
        )

    if (
        getattr(proposal, "status", None) != "review_required"
        or getattr(case, "status", None) != "review_required"
        or getattr(proposal, "company_id", None)
        != getattr(case, "company_id", None)
        or getattr(proposal, "correction_case_id", None)
        != getattr(case, "id", None)
        or getattr(proposal, "submitted_by", None)
        != evidence.submitter_id
    ):
        raise SeveranceApprovalGateError(
            "Correction case or reviewer scope mismatch"
        )

    original = snapshot_from_json(proposal.original_financial)
    corrected = snapshot_from_json(proposal.proposed_financial)

    require_exact_snapshot(
        original,
        evidence.reviewed_original,
    )
    require_exact_snapshot(
        corrected,
        evidence.reviewed_corrected,
    )

    if original == corrected:
        raise SeveranceApprovalGateError(
            "Correction contains no financial difference"
        )

    if original.gross != amount(
        case.original_severance_gross,
        "original severance gross",
        nonnegative=True,
    ):
        raise SeveranceApprovalGateError(
            "Original severance gross differs from pinned case"
        )

    if (
        evidence.statutory_source_verified
        or evidence.journal_posting_authorized
        or evidence.employee_recovery_authorized
        or evidence.tax_refund_authorized
    ):
        raise SeveranceApprovalGateError(
            "Reviewer evidence cannot self-authorize execution"
        )

    return SeveranceApprovalPreflight(
        original=original,
        corrected=corrected,
        gross_delta=corrected.gross - original.gross,
        net_delta=corrected.net - original.net,
        reviewer_separated=True,
        financial_integrity_reconciled=True,
    )
