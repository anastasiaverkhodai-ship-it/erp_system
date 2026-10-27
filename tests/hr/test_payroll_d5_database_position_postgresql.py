
"""Real PostgreSQL D5 position provenance E2E.

Synthetic fixture values are not approved classifier data.
"""
from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_deduction_execution_postgresql import seed

from app.models.employment_event import EmploymentEvent
from app.models.position import Position
from app.services.payroll_d5_event_sources import D5EventSource
from app.services.payroll_d5_verified_mapping import D5ProfessionalEvidence
from app.services.payroll_d5_position_provenance import (
    D5PositionEvidence,
    D5PositionProvenanceError,
)
from app.services.payroll_d5_database_position import (
    verify_database_d5_position,
)

pytestmark = pytest.mark.asyncio


async def test_real_postgresql_position_identity_and_guards(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, actor, contract, calc, bank, beneficiary, claim = (
            await seed(db)
        )
        token = uuid4().hex

        # Test-only HR position, not a certified classifier record.
        position = Position(
            company_id=company.id,
            code="D5-TEST-" + token[:12],
            name="Test position",
            is_active=True,
            created_by=actor.id,
        )
        db.add(position)
        await db.flush()

        event_date = contract.start_date + timedelta(days=2)
        event = EmploymentEvent(
            company_id=company.id,
            employment_contract_id=contract.id,
            event_type="hire",
            effective_date=event_date,
            order_number="D5-" + token,
            order_date=event_date,
            reason="D5 database integration test",
            request_key="D5-DB-" + token,
            before_state={},
            after_state={"position_id": position.id},
            request_payload={},
            created_by=actor.id,
        )
        db.add(event)
        await db.flush()

        source = D5EventSource(
            company_id=company.id,
            employment_event_id=event.id,
            employment_contract_id=contract.id,
            event_type=event.event_type,
            effective_date=event.effective_date,
            order_number=event.order_number,
            order_date=event.order_date,
            termination_basis=None,
        )

        professional = D5ProfessionalEvidence(
            employee_id=contract.employee_id,
            tax_number="TEST-TAX-IDENTITY",
            professional_name="Explicit test-only professional name",
            profession_code="TEST-ONLY",
            position_name="Test position",
            employment_category="primary",
            evidence_reference="test-only-source",
        )
        evidence = D5PositionEvidence(
            company_id=company.id,
            employee_id=contract.employee_id,
            employment_contract_id=contract.id,
            position_id=position.id,
            event_id=event.id,
            professional=professional,
        )

        result = await verify_database_d5_position(
            db, source=source, evidence=evidence
        )
        assert result.source_event_id == event.id
        assert result.contract_id == contract.id

        from dataclasses import replace

        for invalid in (
            replace(evidence, company_id=company.id + 100000),
            replace(evidence, position_id=position.id + 100000),
            replace(evidence, employee_id=contract.employee_id + 100000),
            replace(evidence, event_id=event.id + 100000),
        ):
            with pytest.raises(D5PositionProvenanceError):
                await verify_database_d5_position(
                    db, source=source, evidence=invalid
                )

        # A forged snapshot cannot override the persisted HR event.
        with pytest.raises(D5PositionProvenanceError):
            await verify_database_d5_position(
                db,
                source=replace(source, order_number="FORGED"),
                evidence=evidence,
            )

        # Reversal invalidates the active event source.
        reversal = EmploymentEvent(
            company_id=company.id,
            employment_contract_id=contract.id,
            event_type="reversal",
            effective_date=event_date,
            order_number="REV-" + token,
            order_date=event_date,
            reason="Test reversal",
            request_key="D5-REV-" + token,
            before_state={},
            after_state={},
            request_payload={},
            reversal_of_id=event.id,
            created_by=actor.id,
        )
        db.add(reversal)
        await db.flush()

        with pytest.raises(D5PositionProvenanceError):
            await verify_database_d5_position(
                db, source=source, evidence=evidence
            )

        await db.rollback()
