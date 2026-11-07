"""Real PostgreSQL D5 period completeness acceptance."""

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_d5_database_field_projection_postgresql import (
    setup_case,
)

from app.models.payroll import PayrollPeriod
from app.models.employment_event import EmploymentEvent

from app.services.payroll_d5_period_coverage import (
    D5PeriodCoverageError,
    build_d5_period_coverage_review,
    verify_d5_period_coverage_review,
)


pytestmark = pytest.mark.asyncio


async def setup_period(db):
    company, actor, contract, employee, event, evidence = (
        await setup_case(db, "hire")
    )

    # The existing fixture supplies a payroll period,
    # but its dates may differ from the HR event date.
    # Align the dates inside this disposable test DB only.
    period = await db.scalar(
        select(PayrollPeriod).where(
            PayrollPeriod.company_id == company.id,
        ).order_by(PayrollPeriod.id)
    )

    assert period is not None, (
        "E2E fixture must have a company payroll period"
    )

    if period.start_date > event.effective_date:
        period.start_date = event.effective_date

    if period.end_date < event.effective_date:
        period.end_date = event.effective_date

    await db.flush()

    assert (
        period.start_date
        <= event.effective_date
        <= period.end_date
    ), "Fixture payroll period does not cover HR event"

    targets = [{
        "kind": "event",
        "employment_event_id": event.id,
    }]

    return company, actor, contract, event, period, targets


async def test_pg_complete_coverage_and_missing_event(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        (
            company, actor, contract,
            event, period, targets,
        ) = await setup_period(db)

        kwargs = {
            "company_id": company.id,
            "payroll_period_id": period.id,
            "targets": targets,
        }

        review = await build_d5_period_coverage_review(
            db, **kwargs
        )

        assert review["body"]["active_event_count"] == 1
        assert review["body"]["candidate_target_count"] == 1
        assert review["body"]["official_generation"] == "BLOCKED"
        assert await verify_d5_period_coverage_review(
            db, review
        ) is True

        token = uuid4().hex

        db.add(EmploymentEvent(
            company_id=company.id,
            employment_contract_id=contract.id,
            event_type="hire",
            effective_date=event.effective_date,
            order_number="COVERAGE-" + token,
            order_date=event.effective_date,
            reason="Unrepresented active HR source",
            request_key="COVERAGE-KEY-" + token,
            before_state={},
            after_state={},
            request_payload={},
            created_by=actor.id,
        ))

        await db.flush()

        with pytest.raises(
            D5PeriodCoverageError,
            match="Live period coverage verification failed",
        ):
            await verify_d5_period_coverage_review(
                db, review
            )

        await db.rollback()


async def test_pg_extra_target_and_tampering_rejected(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        (
            company, actor, contract,
            event, period, targets,
        ) = await setup_period(db)

        kwargs = {
            "company_id": company.id,
            "payroll_period_id": period.id,
            "targets": targets,
        }

        with pytest.raises(
            D5PeriodCoverageError,
            match="Incomplete period coverage",
        ):
            await build_d5_period_coverage_review(
                db,
                **{
                    **kwargs,
                    "targets": targets + [{
                        "kind": "event",
                        "employment_event_id": 999999999,
                    }],
                },
            )

        review = await build_d5_period_coverage_review(
            db, **kwargs
        )

        review["body"]["active_event_count"] = 999

        with pytest.raises(
            D5PeriodCoverageError,
            match="Coverage integrity mismatch",
        ):
            await verify_d5_period_coverage_review(
                db, review
            )

        await db.rollback()
