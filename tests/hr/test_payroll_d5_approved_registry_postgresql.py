"""Actual approved bytes through source binding, PostgreSQL and immutable trigger."""
from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_d5_professional_write_postgresql import d5_immutable_engine
from test_payroll_deduction_execution_postgresql import seed
from app.models.position import Position
from app.models.employment_event import EmploymentEvent
from app.models.payroll_d5_professional_evidence import PayrollD5ProfessionalEvidence
from app.services.payroll_d5_event_sources import D5EventSource
from app.services.payroll_d5_verified_mapping import D5ProfessionalEvidence
from app.services.payroll_d5_position_provenance import D5PositionEvidence
from app.services.payroll_d5_professional_history import D5ProfessionalHistoryRecord
from app.services.payroll_d5_approved_registry import create_approved_d5_professional_evidence

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize('day,code,title', [
    (date(2026, 1, 1), '3433', 'Бухгалтер'),
    (date(2026, 4, 13), '1229.3', 'Керівник апарату'),
    (date(2026, 4, 14), '1229.3', 'Керуючий (секретар) виконавчого комітету (апарату)'),
])
async def test_approved_registry_persist_retry_scope_and_immutability(d5_immutable_engine, day, code, title):
    async with AsyncSession(d5_immutable_engine, expire_on_commit=False) as db:
        company, actor, contract, *_ = await seed(db)
        token = uuid4().hex
        position = Position(company_id=company.id, code='APPROVED-'+token[:10],
                            name=title, is_active=True, created_by=actor.id)
        db.add(position)
        await db.flush()
        event = EmploymentEvent(
            company_id=company.id, employment_contract_id=contract.id,
            event_type='hire', effective_date=day, order_number='APPROVED-'+token,
            order_date=day, reason='Isolated production classifier E2E',
            request_key='APPROVED-EVENT-'+token, before_state={},
            after_state={'position_id': position.id}, request_payload={}, created_by=actor.id,
        )
        db.add(event)
        await db.flush()
        source = D5EventSource(
            company_id=company.id, employment_event_id=event.id,
            employment_contract_id=contract.id, event_type='hire', effective_date=day,
            order_number=event.order_number, order_date=day, termination_basis=None,
        )
        professional = D5ProfessionalEvidence(
            employee_id=contract.employee_id, tax_number='TEST-TAX', professional_name=title,
            profession_code=code, position_name=title, employment_category='primary',
            evidence_reference='APPROVED-EVIDENCE-'+token,
        )
        evidence = D5PositionEvidence(
            company_id=company.id, employee_id=contract.employee_id,
            employment_contract_id=contract.id, position_id=position.id,
            event_id=event.id, professional=professional,
        )
        history = D5ProfessionalHistoryRecord(
            company_id=company.id, employee_id=contract.employee_id,
            employment_contract_id=contract.id, position_id=position.id,
            effective_from=day, effective_to=None, documented_on=day,
            evidence_reference=professional.evidence_reference, professional_name=title,
            profession_code=code, position_name=title, employment_category='primary',
        )
        args = dict(source=source, evidence=evidence, history=history,
                    request_key='APPROVED-WRITE-'+token, created_by=actor.id)
        with pytest.raises(ValueError, match='identity mismatch'):
            await create_approved_d5_professional_evidence(
                db, **{**args, 'history': replace(history, company_id=company.id+999)},
            )
        first = await create_approved_d5_professional_evidence(db, **args)
        row_id = first.id
        repeated = await create_approved_d5_professional_evidence(db, **args)
        assert repeated.id == row_id
        assert first.professional_name == title
        assert await db.scalar(select(func.count()).select_from(PayrollD5ProfessionalEvidence)) == 1
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(text('UPDATE payroll_d5_professional_evidence SET professional_name=:name WHERE id=:id'),
                                 {'name': 'Altered after approval', 'id': row_id})
        assert await db.scalar(select(PayrollD5ProfessionalEvidence.professional_name).where(
            PayrollD5ProfessionalEvidence.id == row_id)) == title
        await db.rollback()
