"""Real PostgreSQL D5/D6 inventory and reversal acceptance.

Uses disposable PostgreSQL supplied by the ERP E2E fixture.
Creates no statutory rows and approves no D5/D6 mappings.
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_readiness_postgresql import seed

from app.models.employment_event import EmploymentEvent
from app.models.payroll import PayrollPeriod

from app.services.payroll_d5_event_inventory import (
    load_active_employment_event_records,
)
from app.services.payroll_regulatory_d5_d6_source_control import (
    build_d5_d6_source_control,
    reflected_source_tables,
)
from app.services.payroll_regulatory_d5_d6_workflow import (
    build_verified_d5_d6_workflow,
    compare_hr_reviews,
    verify_review_snapshot,
)


pytestmark = pytest.mark.asyncio


async def add_event(
    db,
    *,
    company_id,
    contract_id,
    actor_id,
    kind,
    day,
    reversal_of_id=None,
):
    token = uuid4().hex

    payload = {}

    if kind == "termination":
        payload["termination_basis"] = {
            "ground": "redundancy",
            "labour_code_provision": "40.1",
            "source_reference": "TEST-ORDER-" + token,
        }

    event = EmploymentEvent(
        company_id=company_id,
        employment_contract_id=contract_id,
        event_type=kind,
        effective_date=day,
        order_number="INV-" + token,
        order_date=day,
        reason="Documented inventory E2E source",
        request_key="inventory-" + token,
        before_state={"position_id": 1},
        after_state={"position_id": 1},
        request_payload=payload,
        reversal_of_id=reversal_of_id,
        created_by=actor_id,
    )

    db.add(event)
    await db.flush()

    return event


def event_section(inventory):
    matches = [
        item for item in inventory["source_inventory"]
        if item["source_table"] == "employment_events"
    ]
    assert len(matches) == 1
    return matches[0]


async def test_real_postgresql_d5_inventory_reversal_and_review(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company, user, contract, calculation, *_ = await seed(db)

        period = await db.get(
            PayrollPeriod,
            calculation.payroll_period_id,
        )

        assert period is not None
        assert period.company_id == company.id

        day = period.start_date
        outside_day = period.end_date + timedelta(days=1)

        # All three event types use genuine persisted database rows.
        hire = await add_event(
            db,
            company_id=company.id,
            contract_id=contract.id,
            actor_id=user.id,
            kind="hire",
            day=day,
        )

        transfer = await add_event(
            db,
            company_id=company.id,
            contract_id=contract.id,
            actor_id=user.id,
            kind="transfer",
            day=day,
        )

        termination = await add_event(
            db,
            company_id=company.id,
            contract_id=contract.id,
            actor_id=user.id,
            kind="termination",
            day=day,
        )

        outside = await add_event(
            db,
            company_id=company.id,
            contract_id=contract.id,
            actor_id=user.id,
            kind="hire",
            day=outside_day,
        )

        initial = await build_d5_d6_source_control(
            db,
            company_id=company.id,
            payroll_period_id=period.id,
        )

        assert initial["review_only"] is True
        assert initial["official_statutory_generation"] == "BLOCKED"
        assert initial["d5"]["official_rows_generated"] == 0
        assert initial["d6"]["official_rows_generated"] == 0

        section = event_section(initial)

        dated = {
            item["source_id"]: item
            for item in section["dated_evidence"]
        }

        expected = {hire.id, transfer.id, termination.id}
        assert expected.issubset(dated)

        for event in (hire, transfer, termination):
            item = dated[event.id]
            assert item["event_date"] == day.isoformat()
            assert item["date_field"] == "effective_date"
            assert item["company_id"] == company.id
            assert item["review_only"] is True
            assert item["statutory_classification"] == "NOT_VERIFIED"
            assert len(item["source_sha256"]) == 64

        assert outside.id not in dated
        assert section["dated_outside_period_count"] >= 1

        # The same underlying facts create a stable review digest.
        review_before = await build_verified_d5_d6_workflow(
            db,
            company_id=company.id,
            payroll_period_id=period.id,
        )

        review_repeat = await build_verified_d5_d6_workflow(
            db,
            company_id=company.id,
            payroll_period_id=period.id,
        )

        assert verify_review_snapshot(review_before) is True
        assert (
            review_before["snapshot_sha256"]
            == review_repeat["snapshot_sha256"]
        )
        assert compare_hr_reviews(
            review_before,
            review_repeat,
        )["change_count"] == 0

        # Create a real PostgreSQL reversal of the transfer.
        reversal = await add_event(
            db,
            company_id=company.id,
            contract_id=contract.id,
            actor_id=user.id,
            kind="reversal",
            day=day,
            reversal_of_id=transfer.id,
        )

        after = await build_d5_d6_source_control(
            db,
            company_id=company.id,
            payroll_period_id=period.id,
        )

        after_section = event_section(after)
        after_ids = {
            item["source_id"]
            for item in after_section["dated_evidence"]
        }

        assert hire.id in after_ids
        assert termination.id in after_ids
        assert transfer.id not in after_ids
        assert reversal.id not in after_ids
        assert outside.id not in after_ids

        # Both raw SQL selection and the reviewed inventory
        # must exclude reversed original events.
        tables = await reflected_source_tables(db)

        active = await load_active_employment_event_records(
            db,
            table=tables["employment_events"],
            company_id=company.id,
            limit=10000,
        )

        active_ids = {item["id"] for item in active}

        assert hire.id in active_ids
        assert termination.id in active_ids
        assert transfer.id not in active_ids
        assert reversal.id not in active_ids
        assert outside.id in active_ids

        # A different company cannot see this company's events.
        foreign = await load_active_employment_event_records(
            db,
            table=tables["employment_events"],
            company_id=company.id + 100000000,
            limit=10000,
        )

        assert foreign == []

        review_after = await build_verified_d5_d6_workflow(
            db,
            company_id=company.id,
            payroll_period_id=period.id,
        )

        assert verify_review_snapshot(review_after) is True

        assert (
            review_before["snapshot_sha256"]
            != review_after["snapshot_sha256"]
        )

        diff = compare_hr_reviews(
            review_before,
            review_after,
        )

        removed = [
            change for change in diff["changes"]
            if change["source_table"] == "employment_events"
            and change["source_id"] == transfer.id
        ]

        assert len(removed) == 1
        assert removed[0]["change_type"] == "REMOVED_SOURCE"

        assert diff["statutory_correction_generated"] is False
        assert diff["official_statutory_generation"] == "BLOCKED"

        assert review_after["review_only"] is True
        assert review_after["d5"]["official_rows_generated"] == 0
        assert review_after["d6"]["official_rows_generated"] == 0

        # Corrupting the review invalidates its cryptographic digest.
        review_after["snapshot"]["records"].append(
            {"forged": True}
        )

        with pytest.raises(Exception):
            verify_review_snapshot(review_after)

        await db.rollback()
