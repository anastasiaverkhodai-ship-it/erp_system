"""Employee tax documentary evidence service.

This module manages evidence records. It does not determine
statutory tax eligibility or perform payroll calculations.

Transaction ownership belongs to the API/application layer.
"""

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee
from app.models.payroll_employee_tax_evidence import (
    PayrollEmployeeTaxEvidence,
)


class PayrollTaxEvidenceError(ValueError):
    """Invalid payroll tax evidence operation."""


ENTITLEMENT_TYPES = {
    "benefit",
    "exemption",
    "individual_rate",
}

VERIFICATION_STATUSES = {
    "pending",
    "verified",
    "rejected",
}


async def create_tax_evidence(
    db: AsyncSession,
    *,
    company_id: int,
    employee_id: int,
    document_type: str,
    document_number: str,
    issued_on: date,
    valid_from: date,
    valid_to: date | None,
    entitlement_type: str,
    entitlement_code: str,
    created_by: int,
) -> PayrollEmployeeTaxEvidence:
    """Register documentary evidence in pending status."""

    employee = await db.scalar(
        select(Employee).where(
            Employee.company_id == company_id,
            Employee.id == employee_id,
        )
    )

    if employee is None:
        raise PayrollTaxEvidenceError(
            "Employee not found in this company"
        )

    document_type = document_type.strip()
    document_number = document_number.strip()
    entitlement_code = entitlement_code.strip()

    if not all((
        document_type,
        document_number,
        entitlement_code,
    )):
        raise PayrollTaxEvidenceError(
            "Document fields cannot be empty"
        )

    if entitlement_type not in ENTITLEMENT_TYPES:
        raise PayrollTaxEvidenceError(
            "Unsupported entitlement type"
        )

    if issued_on > valid_from:
        raise PayrollTaxEvidenceError(
            "Issue date cannot be after validity start"
        )

    if valid_to is not None and valid_to < valid_from:
        raise PayrollTaxEvidenceError(
            "Invalid evidence validity window"
        )

    row = PayrollEmployeeTaxEvidence(
        company_id=company_id,
        employee_id=employee_id,
        document_type=document_type,
        document_number=document_number,
        issued_on=issued_on,
        valid_from=valid_from,
        valid_to=valid_to,
        entitlement_type=entitlement_type,
        entitlement_code=entitlement_code,
        verification_status="pending",
        created_by=created_by,
    )

    db.add(row)
    await db.flush()

    return row


async def get_tax_evidence(
    db: AsyncSession,
    *,
    company_id: int,
    evidence_id: int,
) -> PayrollEmployeeTaxEvidence:
    """Read evidence without crossing company boundaries."""

    row = await db.scalar(
        select(PayrollEmployeeTaxEvidence).where(
            PayrollEmployeeTaxEvidence.company_id == company_id,
            PayrollEmployeeTaxEvidence.id == evidence_id,
        )
    )

    if row is None:
        raise PayrollTaxEvidenceError(
            "Tax evidence not found in this company"
        )

    return row


async def verify_tax_evidence(
    db: AsyncSession,
    *,
    company_id: int,
    evidence_id: int,
    verified_by: int,
    approved: bool,
) -> PayrollEmployeeTaxEvidence:
    """Approve or reject pending evidence.

    This records a review decision; it does not establish
    statutory entitlement by itself.
    """

    row = await db.scalar(
        select(PayrollEmployeeTaxEvidence)
        .where(
            PayrollEmployeeTaxEvidence.company_id == company_id,
            PayrollEmployeeTaxEvidence.id == evidence_id,
        )
        .with_for_update()
    )

    if row is None:
        raise PayrollTaxEvidenceError(
            "Tax evidence not found in this company"
        )

    if row.verification_status != "pending":
        raise PayrollTaxEvidenceError(
            "Evidence has already been reviewed"
        )

    # Four-eyes principle: the creator cannot review
    # their own documentary evidence.
    if row.created_by == verified_by:
        raise PayrollTaxEvidenceError(
            "Evidence cannot be reviewed by its creator"
        )

    # Every review decision must retain its actor and timestamp.
    row.verification_status = (
        "verified" if approved else "rejected"
    )
    row.verified_by = verified_by
    row.verified_at = datetime.now(timezone.utc)

    await db.flush()

    return row


async def require_valid_tax_evidence(
    db: AsyncSession,
    *,
    company_id: int,
    employee_id: int,
    evidence_id: int,
    entitlement_type: str,
    entitlement_code: str,
    effective_on: date,
) -> PayrollEmployeeTaxEvidence:
    """Require verified evidence matching a specific tax basis."""

    row = await get_tax_evidence(
        db,
        company_id=company_id,
        evidence_id=evidence_id,
    )

    if row.employee_id != employee_id:
        raise PayrollTaxEvidenceError(
            "Evidence belongs to another employee"
        )

    if row.verification_status != "verified":
        raise PayrollTaxEvidenceError(
            "Evidence is not verified"
        )

    if row.entitlement_type != entitlement_type:
        raise PayrollTaxEvidenceError(
            "Evidence entitlement type does not match"
        )

    if row.entitlement_code != entitlement_code:
        raise PayrollTaxEvidenceError(
            "Evidence entitlement code does not match"
        )

    if effective_on < row.valid_from:
        raise PayrollTaxEvidenceError(
            "Evidence is not yet valid"
        )

    if row.valid_to is not None and effective_on > row.valid_to:
        raise PayrollTaxEvidenceError(
            "Evidence has expired"
        )

    return row
