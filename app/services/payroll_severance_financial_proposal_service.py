"""Document a proposed severance financial correction.

This is an immutable REVIEW record. A proposed tax allocation is not
independent statutory approval. No payments, accruals, tax remittances,
employee recoveries or accounting journals are created here.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models.company import Company
from app.models.payroll_severance_correction_case import (
    PayrollSeveranceCorrectionCase,
)
from app.models.payroll_severance_financial_proposal import (
    PayrollSeveranceFinancialProposal,
)
from app.services.idempotency_fingerprint_service import (
    generate_request_fingerprint,
)
from app.services.payroll_severance_net_tax_settlement import (
    SeveranceFinancialSnapshot,
)
from app.services.payroll_severance_mixed_financial_snapshot import (
    SeveranceMixedFinancialPreview,
)


class SeveranceFinancialProposalError(ValueError):
    pass


FIELDS = (
    "gross",
    "personal_income_tax",
    "military_levy",
    "net",
    "employer_contribution",
)


def financial_json(snapshot):
    if not isinstance(snapshot, SeveranceFinancialSnapshot):
        raise SeveranceFinancialProposalError(
            "Typed severance financial snapshot required"
        )

    try:
        values = snapshot.validate()
    except (ValueError, ArithmeticError) as exc:
        raise SeveranceFinancialProposalError(
            "Invalid gross, tax or net financial snapshot"
        ) from exc

    return {
        key: str(values[key])
        for key in FIELDS
    }


def validate_financial_proposal(
    *,
    original,
    proposed,
    document_reference,
    document_date,
    request_key,
    submitted_by,
):
    before = financial_json(original)
    after = financial_json(proposed)

    if before == after:
        raise SeveranceFinancialProposalError(
            "Financial proposal contains no change"
        )

    if (
        not isinstance(document_reference, str)
        or not 10 <= len(document_reference.strip()) <= 500
        or type(document_date) is not date
    ):
        raise SeveranceFinancialProposalError(
            "Dated documented correction evidence required"
        )

    if (
        not isinstance(request_key, str)
        or not 8 <= len(request_key.strip()) <= 120
        or type(submitted_by) is not int
        or submitted_by <= 0
    ):
        raise SeveranceFinancialProposalError(
            "Valid submission identity required"
        )

    # The original and corrected snapshots are separately retained.
    # Do not assume that the proposed tax treatment is legally approved.
    return {
        "original_financial": before,
        "proposed_financial": after,
        "document_reference": document_reference.strip(),
        "document_date": document_date.isoformat(),
        "submitted_by": submitted_by,
    }


async def create_severance_financial_proposal(
    db,
    *,
    company_id,
    correction_case_id,
    original_preview,
    proposed_financial,
    document_reference,
    document_date,
    request_key,
    submitted_by,
):
    if (
        type(company_id) is not int
        or company_id <= 0
        or type(correction_case_id) is not int
        or correction_case_id <= 0
        or not isinstance(
            original_preview, SeveranceMixedFinancialPreview
        )
    ):
        raise SeveranceFinancialProposalError(
            "Valid company, case and original preview required"
        )

    payload = validate_financial_proposal(
        original=original_preview.financial,
        proposed=proposed_financial,
        document_reference=document_reference,
        document_date=document_date,
        request_key=request_key,
        submitted_by=submitted_by,
    )

    if (
        original_preview.company_id != company_id
        or not original_preview.statutory_totals_reconciled
        or original_preview.allocation_evidence_approved
        or original_preview.payment_authorized
        or original_preview.tax_correction_authorized
        or original_preview.journal_posting_authorized
    ):
        raise SeveranceFinancialProposalError(
            "Original preview is not a valid unapproved source"
        )

    key = request_key.strip()

    fingerprint = generate_request_fingerprint({
        "company_id": company_id,
        "correction_case_id": correction_case_id,
        "payroll_calculation_id": (
            original_preview.payroll_calculation_id
        ),
        "severance_source_ids": list(
            original_preview.severance_source_ids
        ),
        **payload,
    })

    # Company lock serializes competing proposals and replays.
    owner = await db.scalar(
        select(Company.id)
        .where(Company.id == company_id)
        .with_for_update()
    )

    if owner is None:
        raise SeveranceFinancialProposalError(
            "Company not found"
        )

    case = await db.scalar(
        select(PayrollSeveranceCorrectionCase)
        .where(
            PayrollSeveranceCorrectionCase.company_id
            == company_id,
            PayrollSeveranceCorrectionCase.id
            == correction_case_id,
        )
    )

    if case is None or case.status != "review_required":
        raise SeveranceFinancialProposalError(
            "Active correction case not found"
        )

    if (
        case.original_payroll_calculation_id
        != original_preview.payroll_calculation_id
        or case.original_severance_source_id
        not in original_preview.severance_source_ids
        or Decimal(case.original_severance_gross)
        != original_preview.financial.gross
        or document_date < case.evidence_date
    ):
        raise SeveranceFinancialProposalError(
            "Proposal does not match documented original source"
        )

    old = await db.scalar(
        select(PayrollSeveranceFinancialProposal).where(
            PayrollSeveranceFinancialProposal.company_id
            == company_id,
            PayrollSeveranceFinancialProposal.request_key == key,
        )
    )

    if old is not None:
        if (
            old.request_fingerprint != fingerprint
            or old.correction_case_id != correction_case_id
        ):
            raise SeveranceFinancialProposalError(
                "Idempotency request contains different proposal"
            )
        return old

    other = await db.scalar(
        select(PayrollSeveranceFinancialProposal.id).where(
            PayrollSeveranceFinancialProposal.company_id
            == company_id,
            PayrollSeveranceFinancialProposal.correction_case_id
            == correction_case_id,
        )
    )

    if other is not None:
        raise SeveranceFinancialProposalError(
            "Correction case already has a financial proposal"
        )

    row = PayrollSeveranceFinancialProposal(
        company_id=company_id,
        correction_case_id=correction_case_id,
        request_key=key,
        request_fingerprint=fingerprint,
        document_reference=payload["document_reference"],
        document_date=document_date,
        original_financial=payload["original_financial"],
        proposed_financial=payload["proposed_financial"],
        status="review_required",
        submitted_by=submitted_by,
    )

    db.add(row)
    await db.flush()
    return row
