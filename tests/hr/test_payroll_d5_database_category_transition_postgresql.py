"""Real PostgreSQL historical D5 category transition tests."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_d5_database_field_projection_postgresql import (
    setup_case,
)

from app.models.employment_event import EmploymentEvent
from app.models.position import Position
from app.models.payroll_d5_professional_evidence import (
    PayrollD5ProfessionalEvidence,
)

from app.services.payroll_d5_database_category_transition import (
    D5CategoryTransitionError,
    project_persisted_d5_category_transition,
)


pytestmark = pytest.mark.asyncio


async def setup_transition(db):
    (
        company, actor, contract,
        employee, transfer, current_evidence,
    ) = await setup_case(db, "transfer")

    token = uuid4().hex
    prior_day = contract.start_date

    old_position = Position(
        company_id=company.id,
        code="D5-OLD-" + token[:12],
        name="Previous persisted position",
        is_active=True,
        created_by=actor.id,
    )
    db.add(old_position)
    await db.flush()

    previous_event = EmploymentEvent(
        company_id=company.id,
        employment_contract_id=contract.id,
        event_type="hire",
        effective_date=prior_day,
        order_number="PRIOR-" + token,
        order_date=prior_day,
        reason="Earlier employment source",
        request_key="D5-PRIOR-" + token,
        before_state={},
        after_state={"position_id": old_position.id},
        request_payload={},
        created_by=actor.id,
    )

    db.add(previous_event)
    await db.flush()

    old_evidence = PayrollD5ProfessionalEvidence(
        company_id=company.id,
        employee_id=employee.id,
        employment_contract_id=contract.id,
        employment_event_id=previous_event.id,
        position_id=old_position.id,
        effective_from=prior_day,
        effective_to=transfer.effective_date,
        documented_on=prior_day,
        professional_name="Previous profession",
        profession_code="2419.2",
        position_name=old_position.name,
        employment_category="primary",
        evidence_reference="PRIOR-EVIDENCE-" + token,
        request_key="PRIOR-KEY-" + token,
        created_by=actor.id,
    )

    db.add(old_evidence)

    contract.employment_kind = "internal_secondary"
    current_evidence.employment_category = (
        "internal_secondary"
    )

    transfer.before_state = {
        "employment_kind": "primary",
        "position_id": old_position.id,
    }

    transfer.after_state = {
        "employment_kind": "internal_secondary",
        "position_id": current_evidence.position_id,
    }

    await db.flush()

    return (
        company, actor, contract, employee,
        transfer, current_evidence,
        previous_event, old_evidence,
    )


async def test_real_postgresql_two_row_category_transition(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        (
            company, actor, contract, employee,
            transfer, current, previous, old,
        ) = await setup_transition(db)

        result = await project_persisted_d5_category_transition(
            db,
            company_id=company.id,
            employment_event_id=transfer.id,
            previous_evidence_id=old.id,
        )

        rows = result["candidate"]["candidate_rows"]

        assert len(rows) == 2
        assert [r["fields"]["07"] for r in rows] == [1, 2]
        assert [r["fields"]["12"] for r in rows] == [1, 1]

        expected_date = transfer.effective_date.strftime(
            "%d%m%Y"
        )

        assert rows[0]["fields"]["10"]["end"] == expected_date
        assert rows[1]["fields"]["10"]["start"] == expected_date
        assert rows[0]["fields"]["13"] == old.profession_code
        assert rows[1]["fields"]["13"] == current.profession_code
        assert result["official_generation"] == "BLOCKED"
        assert result["official_rows_generated"] == 0

        repeat = await project_persisted_d5_category_transition(
            db,
            company_id=company.id,
            employment_event_id=transfer.id,
            previous_evidence_id=old.id,
        )

        assert repeat["previous_evidence_sha256"] == (
            result["previous_evidence_sha256"]
        )

        with pytest.raises(
            D5CategoryTransitionError,
            match="Persisted transfer event required",
        ):
            await project_persisted_d5_category_transition(
                db,
                company_id=company.id + 100000000,
                employment_event_id=transfer.id,
                previous_evidence_id=old.id,
            )

        await db.rollback()


async def test_real_postgresql_negative_historical_guards(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        (
            company, actor, contract, employee,
            transfer, current, previous, old,
        ) = await setup_transition(db)

        old.position_id = current.position_id
        await db.flush()

        with pytest.raises(
            D5CategoryTransitionError,
            match="Previous position snapshot mismatch",
        ):
            await project_persisted_d5_category_transition(
                db,
                company_id=company.id,
                employment_event_id=transfer.id,
                previous_evidence_id=old.id,
            )

        old.position_id = transfer.before_state["position_id"]
        await db.flush()

        token = uuid4().hex

        db.add(EmploymentEvent(
            company_id=company.id,
            employment_contract_id=contract.id,
            event_type="reversal",
            effective_date=transfer.effective_date,
            order_number="PRIOR-REV-" + token,
            order_date=transfer.effective_date,
            reason="Reverse preceding HR source",
            request_key="PRIOR-REV-KEY-" + token,
            before_state={},
            after_state={},
            request_payload={},
            reversal_of_id=previous.id,
            created_by=actor.id,
        ))

        await db.flush()

        with pytest.raises(
            D5CategoryTransitionError,
            match="has been reversed",
        ):
            await project_persisted_d5_category_transition(
                db,
                company_id=company.id,
                employment_event_id=transfer.id,
                previous_evidence_id=old.id,
            )

        await db.rollback()
