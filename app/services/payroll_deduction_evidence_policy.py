"""Resolve verified legal evidence for payroll deduction classification.

This module does not authorize payroll withholding or authenticate
legal documents. It never commits or rolls back transactions.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll_deduction_legal_evidence import (
    PayrollDeductionLegalEvidence,
)
from app.services.payroll_deduction_evidence_service import (
    validate_evidence,
)
from app.services.payroll_deduction_legal_classification import (
    DeductionLegalClassification,
    classify_deduction,
)


class PayrollDeductionEvidencePolicyError(ValueError):
    pass


async def resolve_verified_deduction_classification(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_deduction_instruction_id: int,
) -> DeductionLegalClassification:
    """Resolve a candidate classification from persisted evidence."""

    if company_id <= 0:
        raise PayrollDeductionEvidencePolicyError(
            "Invalid company"
        )

    if payroll_deduction_instruction_id <= 0:
        raise PayrollDeductionEvidencePolicyError(
            "Invalid payroll deduction instruction"
        )

    evidence = await db.scalar(
        select(PayrollDeductionLegalEvidence).where(
            PayrollDeductionLegalEvidence.company_id == company_id,
            PayrollDeductionLegalEvidence.payroll_deduction_instruction_id
            == payroll_deduction_instruction_id,
        )
    )

    if evidence is None:
        raise PayrollDeductionEvidencePolicyError(
            "Verified legal evidence is required"
        )

    if evidence.verification_status != "verified":
        raise PayrollDeductionEvidencePolicyError(
            "Legal evidence is not verified"
        )

    try:
        validated = validate_evidence(
            legal_category=evidence.legal_category,
            document_reference=evidence.document_reference,
            verification_status=evidence.verification_status,
            verified_by=evidence.verified_by,
            verified_at=evidence.verified_at,
        )

        return classify_deduction(
            category=validated.legal_category,
            legal_document_verified=True,
            legal_document_reference=validated.document_reference,
        )
    except (ValueError, TypeError, AttributeError) as exc:
        raise PayrollDeductionEvidencePolicyError(
            "Invalid verified legal evidence"
        ) from exc
