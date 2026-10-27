"""Real PostgreSQL positive integration: two completed payroll months.

Runs only on the disposable database copy. Fixture amounts are
illustrative and do not establish statutory income classification.
"""

import os
from calendar import monthrange
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal as D
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_source_locking import sources

from app.models.payroll import (
    PayrollPeriod,
    PayrollInput,
    PayrollCalculation,
    PayrollCalculationLine,
)
from app.models.time_attendance import (
    WorkSchedule,
    WorkScheduleDay,
    EmploymentScheduleAssignment,
    AttendanceRecord,
)
from app.services.payroll_severance_database_average import (
    derive_database_severance_average,
)
from app.services.payroll_severance_average_earnings import (
    SeveranceAverageError,
)


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_E2E") != "1",
        reason="Isolated PostgreSQL required",
    ),
]


async def test_verified_initial_severance_persistence(employee_engine):
    scenario = 'positive'
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        fixtures, contract, _ = await sources(db)

        company_id = fixtures["company"]
        actor_id = fixtures["actor"]

        first = date(2026, 7, 1)
        last = date(2026, 8, 31)

        assert contract.start_date <= first
        assert (
            contract.end_date is None
            or contract.end_date >= last
        )

        # Reuse the fixture's existing schedule, without modifying it.
        assignments = list((
            await db.scalars(
                select(EmploymentScheduleAssignment).where(
                    EmploymentScheduleAssignment.company_id
                    == company_id,
                    EmploymentScheduleAssignment.employment_contract_id
                    == contract.id,
                    EmploymentScheduleAssignment.effective_from
                    <= first,
                    (
                        EmploymentScheduleAssignment.effective_to.is_(None)
                        | (
                            EmploymentScheduleAssignment.effective_to
                            >= last
                        )
                    ),
                )
            )
        ).all())

        assert len(assignments) == 1, (
            "Fixture must have exactly one assignment "
            "covering the entire two-month period"
        )

        assignment = assignments[0]

        schedule = await db.get(
            WorkSchedule, assignment.work_schedule_id
        )

        assert schedule is not None
        assert schedule.company_id == company_id
        assert schedule.is_active is True

        schedule_days = list((
            await db.scalars(
                select(WorkScheduleDay).where(
                    WorkScheduleDay.company_id == company_id,
                    WorkScheduleDay.work_schedule_id == schedule.id,
                )
            )
        ).all())

        by_weekday = {
            day.weekday: day
            for day in schedule_days
        }

        assert set(by_weekday) == {1, 2, 3, 4, 5}

        for weekday in range(1, 6):
            day = by_weekday[weekday]
            assert day.is_working_day is True
            assert day.start_time is not None
            assert day.end_time is not None

        total_days = 0

        for month_number in (7, 8):
            month_start = date(2026, month_number, 1)
            month_end = date(
                2026,
                month_number,
                monthrange(2026, month_number)[1],
            )

            period = PayrollPeriod(
                company_id=company_id,
                year=2026,
                month=month_number,
                start_date=month_start,
                end_date=month_end,
                status=(
                    "draft"
                    if scenario == "unfinalized_period"
                    and month_number == 8
                    else "finalized"
                ),
                created_by=actor_id,
                finalized_by=(
                    None
                    if scenario == "unfinalized_period"
                    and month_number == 8
                    else actor_id
                ),
                finalized_at=(
                    None
                    if scenario == "unfinalized_period"
                    and month_number == 8
                    else datetime.now(timezone.utc)
                ),
            )
            db.add(period)
            await db.flush()

            working_dates = []
            cursor = month_start

            while cursor <= month_end:
                if cursor.isoweekday() <= 5:
                    working_dates.append(cursor)
                cursor += timedelta(days=1)

            for work_date in working_dates:
                start_at = datetime(
                    work_date.year, work_date.month,
                    work_date.day, 9, 0,
                    tzinfo=timezone.utc,
                )
                db.add(
                    AttendanceRecord(
                        company_id=company_id,
                        employment_contract_id=contract.id,
                        work_date=work_date,
                        status="present",
                        actual_start_at=start_at,
                        actual_end_at=start_at
                        + timedelta(hours=8),
                        break_minutes=0,
                        worked_minutes=480,
                        source="system",
                        created_by=actor_id,
                    )
                )

            count = len(working_dates)
            total_days += count

            payroll_input = PayrollInput(
                company_id=company_id,
                payroll_period_id=period.id,
                employment_contract_id=contract.id,
                monthly_norm_minutes=count * 480,
                scheduled_minutes=count * 480,
                worked_minutes=(
                    count * 480 - 480
                    if scenario == "attendance_mismatch"
                    and month_number == 8
                    else count * 480
                ),
                leave_days=0,
                sick_days=0,
                manual_adjustment_amount=D("0.00"),
                source="system",
                created_by=actor_id,
            )
            db.add(payroll_input)
            await db.flush()

            calculation = PayrollCalculation(
                company_id=company_id,
                payroll_period_id=period.id,
                payroll_input_id=payroll_input.id,
                employment_contract_id=contract.id,
                revision=1,
                status="calculated",
                currency_code=(
                    "USD"
                    if scenario == "wrong_currency"
                    and month_number == 8
                    else "UAH"
                ),
                gross_amount=(
                    D("12001.00")
                    if scenario == "gross_mismatch"
                    and month_number == 8
                    else D("12000.00")
                ),
                calculated_by=actor_id,
            )
            db.add(calculation)
            await db.flush()

            db.add(
                PayrollCalculationLine(
                    company_id=company_id,
                    payroll_calculation_id=calculation.id,
                    line_no=1,
                    line_type="salary",
                    description="Verified E2E monthly salary",
                    quantity=D("1"),
                    rate=D("12000"),
                    amount=D("12000.00"),
                    currency_code="UAH",
                )
            )

        await db.flush()

        if scenario != "positive":
            expected_errors = {
                "unfinalized_period": "Finalized complete payroll month",
                "wrong_currency": "Current UAH payroll calculation",
                "gross_mismatch": "Salary lines do not reconcile",
                "attendance_mismatch": "Attendance does not reconcile",
            }
            with pytest.raises(
                SeveranceAverageError,
                match=expected_errors[scenario],
            ):
                await derive_database_severance_average(
                    db,
                    company_id=company_id,
                    employment_contract_id=contract.id,
                    termination_date=date(2026, 9, 30),
                )
            await db.rollback()
            print(f"NEGATIVE {scenario.upper()} = BLOCKED")
            return

        result = await derive_database_severance_average(
            db,
            company_id=company_id,
            employment_contract_id=contract.id,
            termination_date=date(2026, 9, 30),
        )

        expected = (
            D("24000") / D(total_days)
            * (D(total_days) / D("2"))
        ).quantize(D("0.01"))

        assert result.average_monthly == expected
        assert result.average_monthly == D("12000.00")
        assert result.reference_start == first
        assert result.reference_end == last
        assert "revision 1" in result.source_reference

        print("TWO FINALIZED MONTHS = PASS")
        print("ATTENDANCE AND SCHEDULE = PASS")
        print("CURRENT PAYROLL REVISIONS = PASS")
        print("AVERAGE MONTHLY = 12000.00 UAH")


        from app.schemas.employment_event import EmploymentEventCreate
        from app.services.employment_event_service import create_employment_event
        from app.models.payroll_termination_severance import PayrollTerminationSeverance
        from app.services.payroll_severance_verified_source_service import (
            create_initial_verified_severance_source,
            VerifiedSeveranceSourceError,
        )

        event = await create_employment_event(
            db,
            company_id=company_id,
            contract_id=contract.id,
            created_by=actor_id,
            data=EmploymentEventCreate(
                event_type="termination",
                effective_date=date(2026, 9, 30),
                order_number="SEV-PG-2026-09",
                order_date=date(2026, 9, 30),
                reason="Documented isolated redundancy",
                request_key="severance-pg-event",
            ),
        )

        event.request_payload = {
            **(event.request_payload or {}),
            "termination_basis": {
                "ground": "redundancy",
                "labour_code_provision": "40.1",
                "source_reference": "Isolated E2E redundancy order",
            },
        }
        await db.flush()

        settlement_period = PayrollPeriod(
            company_id=company_id,
            year=2026,
            month=9,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 30),
            status="draft",
            created_by=actor_id,
        )
        db.add(settlement_period)
        await db.flush()

        settlement_input = PayrollInput(
            company_id=company_id,
            payroll_period_id=settlement_period.id,
            employment_contract_id=contract.id,
            monthly_norm_minutes=None,
            scheduled_minutes=0,
            worked_minutes=0,
            leave_days=0,
            sick_days=0,
            manual_adjustment_amount=D("0.00"),
            source="manual",
            created_by=actor_id,
        )
        db.add(settlement_input)
        await db.flush()

        saved = await create_initial_verified_severance_source(
            db,
            company_id=company_id,
            employment_contract_id=contract.id,
            payroll_input_id=settlement_input.id,
            request_key="severance-pg-source-r1",
            created_by=actor_id,
        )

        assert saved.revision == 1
        assert saved.supersedes_id is None
        assert saved.base_amount == D("12000.00")
        assert saved.gross_amount == D("12000.00")
        assert saved.policy_category == "average_months"
        assert saved.policy_ground == "redundancy"
        assert saved.termination_event_id == event.id

        count = len(list((
            await db.scalars(
                select(PayrollTerminationSeverance).where(
                    PayrollTerminationSeverance.company_id == company_id,
                    PayrollTerminationSeverance.termination_event_id == event.id,
                )
            )
        ).all()))
        assert count == 1

        replay = await create_initial_verified_severance_source(
            db,
            company_id=company_id,
            employment_contract_id=contract.id,
            payroll_input_id=settlement_input.id,
            request_key="severance-pg-source-r1",
            created_by=actor_id,
        )

        assert replay.id == saved.id
        assert replay.revision == 1

        replay_rows = list((
            await db.scalars(
                select(PayrollTerminationSeverance).where(
                    PayrollTerminationSeverance.company_id
                    == company_id,
                    PayrollTerminationSeverance.termination_event_id
                    == event.id,
                )
            )
        ).all())

        assert len(replay_rows) == 1
        print("EXACT IDEMPOTENT REPLAY = PASS")
        print("REPLAY SOURCE ROW COUNT = 1")

        with pytest.raises(VerifiedSeveranceSourceError):
            await create_initial_verified_severance_source(
                db,
                company_id=company_id,
                employment_contract_id=contract.id,
                payroll_input_id=settlement_input.id,
                request_key="different-initial-request",
                created_by=actor_id,
            )

        print("INITIAL SOURCE REVISION 1 = PASS")
        print("IMMUTABLE AMOUNT = 12000.00 UAH")
        print("DUPLICATE INITIAL SOURCE = BLOCKED")


        from dataclasses import replace
        from unittest.mock import patch
        from app.services.payroll_severance_verified_correction_service import (
            CorrectionEvidence,
            create_verified_severance_correction,
        )
        from app.services.payroll_severance_verified_basis_service import (
            prepare_verified_severance_basis as real_prepare_basis,
        )

        correction_evidence = CorrectionEvidence(
            reference="CORRECTION-ORDER-2026-10-15",
            effective_date=date(2026, 10, 15),
        )

        # Real PostgreSQL and unmodified ERP payroll data.
        # The same verified earnings must not create a correction.
        with pytest.raises(
            VerifiedSeveranceSourceError,
            match="No changed verified source evidence",
        ):
            await create_verified_severance_correction(
                db,
                company_id=company_id,
                employment_contract_id=contract.id,
                payroll_input_id=settlement_input.id,
                previous_source_id=saved.id,
                request_key="sev-pg-r2-unchanged",
                created_by=actor_id,
                evidence=correction_evidence,
            )

        print("UNCHANGED REAL PAYROLL BASIS = BLOCKED")

        before = (
            saved.id,
            saved.revision,
            saved.supersedes_id,
            saved.base_amount,
            saved.gross_amount,
            saved.basis_reference,
            saved.request_key,
        )

        # Controlled corrected evidence for persistence-chain testing.
        # This tests revision mechanics, NOT genuine payroll correction.
        original_basis = await real_prepare_basis(
            db,
            company_id=company_id,
            employment_contract_id=contract.id,
            payroll_input_id=settlement_input.id,
        )

        corrected_basis = replace(
            original_basis,
            base_amount=D("12500.00"),
            gross_amount=D("12500.00"),
            basis_reference=(
                original_basis.basis_reference
                + "; CONTROLLED CORRECTION TEST"
            ),
        )

        async def controlled_basis(db_arg, **kwargs):
            assert kwargs["company_id"] == company_id
            assert kwargs["employment_contract_id"] == contract.id
            assert kwargs["payroll_input_id"] == settlement_input.id
            return corrected_basis

        with patch(
            "app.services."
            "payroll_severance_verified_correction_service."
            "prepare_verified_severance_basis",
            new=controlled_basis,
        ):
            corrected = await create_verified_severance_correction(
                db,
                company_id=company_id,
                employment_contract_id=contract.id,
                payroll_input_id=settlement_input.id,
                previous_source_id=saved.id,
                request_key="sev-pg-r2-controlled",
                created_by=actor_id,
                evidence=correction_evidence,
            )

            assert corrected.revision == 2
            assert corrected.supersedes_id == saved.id
            assert corrected.base_amount == D("12500.00")
            assert corrected.gross_amount == D("12500.00")

            # A second branch from revision 1 must fail.
            with pytest.raises(
                VerifiedSeveranceSourceError,
                match="latest source",
            ):
                await create_verified_severance_correction(
                    db,
                    company_id=company_id,
                    employment_contract_id=contract.id,
                    payroll_input_id=settlement_input.id,
                    previous_source_id=saved.id,
                    request_key="sev-pg-r2-branch",
                    created_by=actor_id,
                    evidence=correction_evidence,
                )

        await db.refresh(saved)

        after = (
            saved.id,
            saved.revision,
            saved.supersedes_id,
            saved.base_amount,
            saved.gross_amount,
            saved.basis_reference,
            saved.request_key,
        )

        assert after == before

        versions = list((
            await db.scalars(
                select(PayrollTerminationSeverance).where(
                    PayrollTerminationSeverance.company_id == company_id,
                    PayrollTerminationSeverance.termination_event_id
                    == event.id,
                ).order_by(PayrollTerminationSeverance.revision)
            )
        ).all())

        assert len(versions) == 2
        assert [row.revision for row in versions] == [1, 2]
        assert versions[0].id == saved.id
        assert versions[1].supersedes_id == versions[0].id
        assert versions[0].gross_amount == D("12000.00")
        assert versions[1].gross_amount == D("12500.00")

        print("REVISION 1 UNCHANGED = PASS")
        print("REVISION 2 CHAIN = PASS")
        print("REVISION 2 BRANCH = BLOCKED")
        print("REVISION 2 CONTROLLED EVIDENCE = PASS")

        await db.rollback()
