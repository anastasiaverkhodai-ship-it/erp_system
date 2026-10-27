"""PostgreSQL contract for internal D5 event-source selection.

These tests do not certify statutory D5 generation.
"""

from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_deduction_execution_postgresql import seed

from app.models.employment_event import EmploymentEvent
from app.services.payroll_d5_event_sources import (
    D5EventSourceError,
    collect_d5_event_sources,
)

pytestmark = pytest.mark.asyncio


async def _event(
    db,
    *,
    company_id,
    contract_id,
    actor_id,
    event_type,
    day,
    reversal_of_id=None,
):
    token = uuid4().hex
    payload = {}

    if event_type == "termination":
        payload["termination_basis"] = {
            "ground": "redundancy",
            "labour_code_provision": "40.1",
            "source_reference": "documented-order-" + token,
        }

    event = EmploymentEvent(
        company_id=company_id,
        employment_contract_id=contract_id,
        event_type=event_type,
        effective_date=day,
        order_number="E2E-" + token,
        order_date=day,
        reason="Documented E2E source",
        request_key="d5-e2e-" + token,
        before_state={},
        after_state={},
        request_payload=payload,
        reversal_of_id=reversal_of_id,
        created_by=actor_id,
    )

    db.add(event)
    await db.flush()
    return event


async def test_d5_sources_real_postgresql_reversal_and_scope(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, actor, contract, calc, bank, beneficiary, claim = (
            await seed(db)
        )

        start = contract.start_date
        assert isinstance(start, date)

        hire = await _event(
            db,
            company_id=company.id,
            contract_id=contract.id,
            actor_id=actor.id,
            event_type="hire",
            day=start,
        )
        transfer = await _event(
            db,
            company_id=company.id,
            contract_id=contract.id,
            actor_id=actor.id,
            event_type="transfer",
            day=start + timedelta(days=10),
        )
        termination = await _event(
            db,
            company_id=company.id,
            contract_id=contract.id,
            actor_id=actor.id,
            event_type="termination",
            day=start + timedelta(days=20),
        )

        await db.flush()

        rows = await collect_d5_event_sources(
            db,
            company_id=company.id,
            date_from=start,
            date_to=start + timedelta(days=30),
        )

        expected_ids = {
            hire.id, transfer.id, termination.id
        }
        assert expected_ids.issubset({
            item.employment_event_id for item in rows
        })

        termination_source = next(
            item for item in rows
            if item.employment_event_id == termination.id
        )
        assert termination_source.termination_basis["ground"] == (
            "redundancy"
        )

        await _event(
            db,
            company_id=company.id,
            contract_id=contract.id,
            actor_id=actor.id,
            event_type="reversal",
            day=start + timedelta(days=21),
            reversal_of_id=transfer.id,
        )

        rows_after = await collect_d5_event_sources(
            db,
            company_id=company.id,
            date_from=start,
            date_to=start + timedelta(days=30),
        )

        active_ids = {
            item.employment_event_id for item in rows_after
        }

        assert hire.id in active_ids
        assert termination.id in active_ids
        assert transfer.id not in active_ids

        # Other-company scope must not expose company A's events.
        outside = await collect_d5_event_sources(
            db,
            company_id=company.id + 100000000,
            date_from=start,
            date_to=start + timedelta(days=30),
        )
        assert outside == ()

        # Inclusive date boundaries.
        just_termination = await collect_d5_event_sources(
            db,
            company_id=company.id,
            date_from=termination.effective_date,
            date_to=termination.effective_date,
        )
        assert termination.id in {
            item.employment_event_id
            for item in just_termination
        }

        # This collector must be read-only.
        count_before = await db.scalar(
            select(func.count()).select_from(EmploymentEvent).where(
                EmploymentEvent.company_id == company.id
            )
        )
        await collect_d5_event_sources(
            db,
            company_id=company.id,
            date_from=start,
            date_to=start + timedelta(days=30),
        )
        count_after = await db.scalar(
            select(func.count()).select_from(EmploymentEvent).where(
                EmploymentEvent.company_id == company.id
            )
        )
        assert count_before == count_after

        await db.rollback()


async def test_d5_sources_invalid_arguments_reject_without_query(
    employee_engine,
):
    class ForbiddenDB:
        async def scalars(self, statement):
            raise AssertionError("Unexpected database query")

    db = ForbiddenDB()

    cases = (
        (0, date(2026, 1, 1), date(2026, 1, 31)),
        (True, date(2026, 1, 1), date(2026, 1, 31)),
        (1, date(2026, 2, 1), date(2026, 1, 31)),
        (1, datetime(2026, 1, 1), date(2026, 1, 31)),
    )

    for company, start, end in cases:
        with pytest.raises(D5EventSourceError):
            await collect_d5_event_sources(
                db,
                company_id=company,
                date_from=start,
                date_to=end,
            )
