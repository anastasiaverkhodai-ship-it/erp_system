"""Create pending legal evidence for a payroll deduction instruction.

Transaction ownership belongs to the caller.

This service does not verify legal documents, approve deductions,
or grant access to payroll records.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll_deduction import PayrollDeductionInstruction
from app.models.payroll_deduction_legal_evidence import (
    PayrollDeductionLegalEvidence,
)
from app.services.payroll_deduction_evidence_service import (
    validate_evidence,
)


class PayrollDeductionEvidenceWriteError(ValueError):
    pass


async def create_pending_deduction_evidence(
    *,
    db: AsyncSession,
    company_id: int,
    payroll_deduction_instruction_id: int,
    legal_category: str,
    document_reference: str,
    created_by: int,
) -> PayrollDeductionLegalEvidence:
    """Create a pending record for an existing company-owned instruction.

    The caller must have already authenticated and authorized created_by.
    A unique database constraint protects against duplicate records.
    """

    if company_id <= 0:
        raise PayrollDeductionEvidenceWriteError(
            "Invalid company"
        )

    if payroll_deduction_instruction_id <= 0:
        raise PayrollDeductionEvidenceWriteError(
            "Invalid payroll deduction instruction"
        )

    if created_by <= 0:
        raise PayrollDeductionEvidenceWriteError(
            "Invalid actor"
        )

    validated = validate_evidence(
        legal_category=legal_category,
        document_reference=document_reference,
        verification_status="pending",
        verified_by=None,
        verified_at=None,
    )

    instruction = await db.scalar(
        select(PayrollDeductionInstruction).where(
            PayrollDeductionInstruction.id
            == payroll_deduction_instruction_id,
            PayrollDeductionInstruction.company_id
            == company_id,
        )
    )

    if instruction is None:
        raise PayrollDeductionEvidenceWriteError(
            "Payroll deduction instruction not found"
        )

    existing = await db.scalar(
        select(PayrollDeductionLegalEvidence).where(
            PayrollDeductionLegalEvidence.company_id
            == company_id,
            PayrollDeductionLegalEvidence.payroll_deduction_instruction_id
            == payroll_deduction_instruction_id,
        )
    )

    if existing is not None:
        raise PayrollDeductionEvidenceWriteError(
            "Legal evidence already exists for instruction"
        )

    evidence = PayrollDeductionLegalEvidence(
        company_id=company_id,
        payroll_deduction_instruction_id=(
            payroll_deduction_instruction_id
        ),
        legal_category=validated.legal_category,
        document_reference=validated.document_reference,
        verification_status="pending",
        verified_by=None,
        verified_at=None,
        created_by=created_by,
        created_at=datetime.now(timezone.utc),
    )

    db.add(evidence)
    await db.flush()

    return evidence
