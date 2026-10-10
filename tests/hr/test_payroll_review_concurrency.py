"""A cached ORM object must not overwrite a committed review or policy end."""
import os
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_vacation_postgresql import _seed_identity
from app.models.user import User
from app.models.payroll_tax import PayrollEmployeeTaxProfile
from app.services.payroll_tax_evidence_service import (
    create_tax_evidence, get_tax_evidence, verify_tax_evidence, PayrollTaxEvidenceError,
)
from app.services.payroll_tax_policy_service import end_tax_policy
from app.services.payroll_statutory_service import PayrollStatutoryValidationError

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv('RUN_POSTGRES_E2E') != '1', reason='PostgreSQL required')]


async def test_stale_pending_evidence_cannot_overwrite_review(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as cached:
        company, user, employee, contract = await _seed_identity(cached)
        reviewer = User(email=f'review-{uuid4().hex}@example.test', password_hash='test',
                        first_name='Test', last_name='Reviewer', is_active=True)
        cached.add(reviewer)
        await cached.flush()
        company_id, reviewer_id = company.id, reviewer.id
        row = await create_tax_evidence(cached, company_id=company_id, employee_id=employee.id,
            document_type='test', document_number='1', issued_on=date(2026,1,1),
            valid_from=date(2026,1,1), valid_to=None, entitlement_type='benefit',
            entitlement_code='test', created_by=user.id)
        evidence_id = row.id
        await cached.commit()
        async with AsyncSession(employee_engine) as other:
            await verify_tax_evidence(other, company_id=company_id, evidence_id=evidence_id,
                                      verified_by=reviewer_id, approved=False)
            await other.commit()
        assert row.verification_status == 'pending'
        with pytest.raises(PayrollTaxEvidenceError, match='already been reviewed'):
            await verify_tax_evidence(cached, company_id=company_id, evidence_id=evidence_id,
                                      verified_by=reviewer_id, approved=True)
        await cached.rollback()
        actual = await get_tax_evidence(cached, company_id=company_id, evidence_id=evidence_id)
        assert actual.verification_status == 'rejected'
        assert actual.verified_by == reviewer_id


async def test_stale_policy_cannot_extend_committed_end(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as cached:
        company, user, employee, contract = await _seed_identity(cached)
        row = PayrollEmployeeTaxProfile(company_id=company.id, employee_id=employee.id,
            employment_contract_id=contract.id, category='standard',
            effective_from=date(2026,1,1), created_by=user.id)
        cached.add(row)
        await cached.flush()
        company_id, policy_id = company.id, row.id
        await cached.commit()
        async with AsyncSession(employee_engine) as other:
            await end_tax_policy(other, company_id=company_id, policy_id=policy_id,
                                 effective_to=date(2026,9,30), profile=True)
            await other.commit()
        assert row.effective_to is None
        with pytest.raises(PayrollStatutoryValidationError, match='shorten'):
            await end_tax_policy(cached, company_id=company_id, policy_id=policy_id,
                                 effective_to=date(2026,10,31), profile=True)
        assert row.effective_to == date(2026,9,30)
