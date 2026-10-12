"""Controlled verification of payroll deduction legal evidence.

Caller responsibilities:
- authenticate verifier;
- authorize verifier for the company and payroll operation;
- independently inspect the legal document;
- own the database transaction.

This service does not commit or roll back.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll_deduction_legal_evidence import (
    PayrollDeductionLegalEvidence,
)
from app.services.payroll_deduction_evidence_service import (
    validate_evidence,
)


class PayrollDeductionEvidenceVerificationError(ValueError):
    pass


async def verify_deduction_evidence(
    *,
    db: AsyncSession,
    company_id: int,
    evidence_id: int,
    verified_by: int,
) -> PayrollDeductionLegalEvidence:
    """Verify a pending evidence record.

    Row locking prevents two concurrent transactions from
    successfully verifying the same evidence record.
    """

    if company_id <= 0:
        raise PayrollDeductionEvidenceVerificationError(
            "Invalid company"
        )

    if evidence_id <= 0:
        raise PayrollDeductionEvidenceVerificationError(
            "Invalid evidence identifier"
        )

    if verified_by <= 0:
        raise PayrollDeductionEvidenceVerificationError(
            "Invalid verifier"
        )

    statement = (
        select(PayrollDeductionLegalEvidence)
        .where(
            PayrollDeductionLegalEvidence.id == evidence_id,
            PayrollDeductionLegalEvidence.company_id == company_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    evidence = await db.scalar(statement)

    if evidence is None:
        raise PayrollDeductionEvidenceVerificationError(
            "Legal evidence not found"
        )

    if evidence.verification_status != "pending":
        raise PayrollDeductionEvidenceVerificationError(
            "Only pending evidence can be verified"
        )

    verified_at = datetime.now(timezone.utc)

    validate_evidence(
        legal_category=evidence.legal_category,
        document_reference=evidence.document_reference,
        verification_status="verified",
        verified_by=verified_by,
        verified_at=verified_at,
    )

    evidence.verification_status = "verified"
    evidence.verified_by = verified_by
    evidence.verified_at = verified_at

    await db.flush()

    return evidence
