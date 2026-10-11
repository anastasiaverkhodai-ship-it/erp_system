"""Evidence records retain the original document and an independent review."""
import hashlib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select
from app.models.employee import Employee
from app.models.payroll_insurance_evidence import PayrollInsuranceEvidence
from app.services.payroll_mutation_guard import serialized_payroll_mutation


class PayrollInsuranceEvidenceError(ValueError):
    pass


class PayrollInsuranceEvidenceNotFoundError(PayrollInsuranceEvidenceError):
    pass


async def get_insurance_evidence(db, *, company_id, evidence_id, lock=False):
    query = select(PayrollInsuranceEvidence).where(
        PayrollInsuranceEvidence.company_id == company_id, PayrollInsuranceEvidence.id == evidence_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = await db.scalar(query)
    if row is None:
        raise PayrollInsuranceEvidenceNotFoundError('Insurance evidence not found')
    return row


@serialized_payroll_mutation(PayrollInsuranceEvidenceError)
async def create_insurance_evidence(db, *, company_id, data, created_by):
    fingerprint = hashlib.sha256(data.model_dump_json().encode()).hexdigest()
    previous = await db.scalar(select(PayrollInsuranceEvidence).where(
        PayrollInsuranceEvidence.company_id == company_id, PayrollInsuranceEvidence.request_key == data.request_key))
    if previous:
        if previous.request_fingerprint != fingerprint:
            raise PayrollInsuranceEvidenceError('Request key has different insurance evidence')
        return previous
    if not await db.scalar(select(Employee.id).where(Employee.company_id == company_id, Employee.id == data.employee_id)):
        raise PayrollInsuranceEvidenceNotFoundError('Employee not found in this company')
    today = datetime.now(ZoneInfo('Europe/Kyiv')).date()
    if data.case_start > today or data.issued_on > today:
        raise PayrollInsuranceEvidenceError('Insurance case and evidence issue date cannot be in the future')
    row = PayrollInsuranceEvidence(company_id=company_id, **data.model_dump(),
        request_fingerprint=fingerprint, created_by=created_by, status='pending')
    db.add(row)
    await db.flush()
    return row


@serialized_payroll_mutation(PayrollInsuranceEvidenceError)
async def review_insurance_evidence(db, *, company_id, evidence_id, reviewed_by, approved):
    row = await get_insurance_evidence(db, company_id=company_id, evidence_id=evidence_id, lock=True)
    if row.status != 'pending':
        raise PayrollInsuranceEvidenceError('Insurance evidence has already been reviewed')
    if reviewed_by == row.created_by:
        raise PayrollInsuranceEvidenceError('Insurance evidence requires an independent reviewer')
    row.status = 'verified' if approved else 'rejected'
    row.reviewed_by = reviewed_by
    row.reviewed_at = datetime.now(timezone.utc)
    await db.flush()
    return row
