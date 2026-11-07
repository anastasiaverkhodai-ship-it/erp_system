"""Real PostgreSQL acceptance for the DB-bound D5 projection."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_deduction_execution_postgresql import seed

from app.models.employee import Employee
from app.models.position import Position
from app.models.employment_event import EmploymentEvent
from app.models.payroll_d5_professional_evidence import (
    PayrollD5ProfessionalEvidence,
)

from app.services.payroll_d5_database_field_projection import (
    D5DatabaseProjectionError,
    project_persisted_d5_event,
)


pytestmark = pytest.mark.asyncio


async def setup_case(db, kind="hire"):
    company, actor, contract, *_ = await seed(db)

    token = uuid4().hex
    day = contract.start_date + timedelta(days=2)

    employee = await db.get(
        Employee, contract.employee_id
    )
    assert employee is not None

    employee.tax_number = "1234567890"
    contract.employment_kind = "primary"

    position = Position(
        company_id=company.id,
        code="D5-PROJ-" + token[:12],
        name="Synthetic verified position",
        is_active=True,
        created_by=actor.id,
    )

    db.add(position)
    await db.flush()

    contract.position_id = position.id

    payload = {}

    if kind == "termination":
        payload["termination_basis"] = {
            "ground": "redundancy",
            "labour_code_provision": "40.1",
            "source_reference": "PG-ORDER-" + token,
        }

    event = EmploymentEvent(
        company_id=company.id,
        employment_contract_id=contract.id,
        event_type=kind,
        effective_date=day,
        order_number="PG-EVENT-" + token,
        order_date=day,
        reason="Source-bound D5 E2E",
        request_key="D5-PROJECTION-" + token,
        before_state={},
        after_state={"position_id": position.id},
        request_payload=payload,
        created_by=actor.id,
    )

    db.add(event)
    await db.flush()

    evidence = PayrollD5ProfessionalEvidence(
        company_id=company.id,
        employee_id=employee.id,
        employment_contract_id=contract.id,
        employment_event_id=event.id,
        position_id=position.id,
        effective_from=day,
        effective_to=None,
        documented_on=day,
        professional_name="Synthetic verified profession",
        profession_code="2419.2",
        position_name=position.name,
        employment_category="primary",
        evidence_reference="PG-EVIDENCE-" + token,
        request_key="PG-EVIDENCE-KEY-" + token,
        created_by=actor.id,
    )

    db.add(evidence)
    await db.flush()

    return company, actor, contract, employee, event, evidence


async def test_real_postgresql_projection_and_rejections(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        (
            company, actor, contract,
            employee, event, evidence,
        ) = await setup_case(db, "hire")

        result = await project_persisted_d5_event(
            db,
            company_id=company.id,
            employment_event_id=event.id,
        )

        fields = result["candidate"]["fields"]

        assert fields["07"] == 1
        assert fields["08"] == employee.tax_number
        assert fields["10"]["start"] == (
            event.effective_date.strftime("%d%m%Y")
        )
        assert fields["13"] == evidence.profession_code
        assert fields["15"] == evidence.position_name
        assert fields["16"] == event.order_number

        assert result["source_binding"] == (
            "PERSISTED_POSTGRESQL_RECORDS_VERIFIED"
        )
        assert result["official_generation"] == "BLOCKED"
        assert result["official_rows_generated"] == 0

        repeat = await project_persisted_d5_event(
            db,
            company_id=company.id,
            employment_event_id=event.id,
        )

        assert (
            repeat["persisted_erp_evidence_sha256"]
            == result["persisted_erp_evidence_sha256"]
        )

        with pytest.raises(
            D5DatabaseProjectionError,
            match="not found",
        ):
            await project_persisted_d5_event(
                db,
                company_id=company.id + 100000000,
                employment_event_id=event.id,
            )

        evidence.employment_category = "external_secondary"
        await db.flush()

        with pytest.raises(
            D5DatabaseProjectionError,
            match="category",
        ):
            await project_persisted_d5_event(
                db,
                company_id=company.id,
                employment_event_id=event.id,
            )

        evidence.employment_category = "primary"
        evidence.effective_from = (
            event.effective_date + timedelta(days=1)
        )
        await db.flush()

        with pytest.raises(
            D5DatabaseProjectionError,
            match="not effective",
        ):
            await project_persisted_d5_event(
                db,
                company_id=company.id,
                employment_event_id=event.id,
            )

        await db.rollback()


async def test_real_postgresql_reversed_event_is_blocked(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        (
            company, actor, contract,
            employee, event, evidence,
        ) = await setup_case(db, "transfer")

        before = await project_persisted_d5_event(
            db,
            company_id=company.id,
            employment_event_id=event.id,
        )

        assert before["candidate"]["fields"]["12"] == 1

        token = uuid4().hex

        db.add(EmploymentEvent(
            company_id=company.id,
            employment_contract_id=contract.id,
            event_type="reversal",
            effective_date=event.effective_date,
            order_number="REVERSAL-" + token,
            order_date=event.effective_date,
            reason="Reverse source event",
            request_key="D5-REVERSAL-" + token,
            before_state={},
            after_state={},
            request_payload={},
            reversal_of_id=event.id,
            created_by=actor.id,
        ))

        await db.flush()

        with pytest.raises(
            D5DatabaseProjectionError,
            match="non-reversed",
        ):
            await project_persisted_d5_event(
                db,
                company_id=company.id,
                employment_event_id=event.id,
            )

        await db.rollback()
