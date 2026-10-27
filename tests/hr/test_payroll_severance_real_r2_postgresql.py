"""Real September/October corrected payroll -> Order 100 average.

PostgreSQL only. Never execute against the primary ERP database.
"""
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_correction_basis_postgresql import protect
import test_payroll_minimum_wage_postgresql as wage_fixture

from app.models.payroll import (
    PayrollCalculation,
    PayrollCalculationLine,
    PayrollInputSalarySlice,
)
from app.services.payroll_calculation_service import (
    calculate_payroll_input,
)
from app.services.payroll_correction_source_evidence_service import (
    capture_payroll_correction_source,
)
from app.services.payroll_correction_recalculation_service import (
    recalculate_minimum_wage_correction,
)
from app.services.payroll_severance_database_average import (
    derive_database_severance_average,
)
from app.services.payroll_severance_minimum_wage_provenance import (
    derive_verified_earnings_candidate,
)


@pytest.mark.asyncio
async def test_genuine_severance_r1_payroll_correction_r2(
    employee_engine,
):
    await protect(employee_engine)

    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        try:
            # September: existing real ERP fixture.
            fixture, september_input = await wage_fixture.inputs(
                db,
                part_time=True,
            )
            company_id = fixture["company"]
            actor_id = fixture["actor"]

            original_context = {
                "full_time_norm_minutes": 10560,
                "norm_reference": "Calendar original",
            }

            original = await calculate_payroll_input(
                db,
                company_id=company_id,
                payroll_input_id=september_input.id,
                calculated_by=actor_id,
                minimum_wage_context=original_context,
            )

            # August: create genuine period, attendance, payroll input
            # and salary snapshots using normal ERP services.
            contract_id = september_input.employment_contract_id

            august_period = await wage_fixture.create_payroll_period(
                db,
                company_id=company_id,
                created_by=actor_id,
                data=wage_fixture.PayrollPeriodCreate(
                    year=2026,
                    month=8,
                    start_date=date(2026, 8, 1),
                    end_date=date(2026, 8, 31),
                ),
            )

            for day in range(1, 32):
                work_date = date(2026, 8, day)
                if work_date.weekday() >= 5:
                    continue

                await wage_fixture.create_attendance(
                    db,
                    company_id=company_id,
                    created_by=actor_id,
                    data=wage_fixture.AttendanceCreate(
                        employment_contract_id=contract_id,
                        work_date=work_date,
                        status="present",
                        actual_start_at=datetime(
                            2026, 8, day, 9,
                            tzinfo=ZoneInfo("Europe/Kyiv"),
                        ),
                        actual_end_at=datetime(
                            2026, 8, day, 13,
                            tzinfo=ZoneInfo("Europe/Kyiv"),
                        ),
                    ),
                )

            august_input = await wage_fixture.create_payroll_input(
                db,
                company_id=company_id,
                payroll_period_id=august_period.id,
                created_by=actor_id,
                data=wage_fixture.PayrollInputCreate(
                    employment_contract_id=contract_id,
                    manual_adjustment_amount=Decimal("0.00"),
                ),
            )

            await wage_fixture.finalize_payroll_period(
                db,
                company_id=company_id,
                payroll_period_id=august_period.id,
                finalized_by=actor_id,
            )

            august = await calculate_payroll_input(
                db,
                company_id=company_id,
                payroll_input_id=august_input.id,
                calculated_by=actor_id,
                minimum_wage_context={
                    "full_time_norm_minutes": 10560,
                    "norm_reference": "August documented calendar",
                },
            )

            assert august.revision == 1

            from app.schemas.employment_event import EmploymentEventCreate
            from app.services.employment_event_service import (
                create_employment_event,
            )
            from app.models.payroll import PayrollInput, PayrollPeriod
            from app.models.payroll_termination_severance import (
                PayrollTerminationSeverance,
            )
            from app.services.payroll_severance_verified_source_service import (
                create_initial_verified_severance_source,
            )
            from app.services.payroll_severance_verified_correction_service import (
                CorrectionEvidence,
                VerifiedSeveranceSourceError,
                create_verified_severance_correction,
            )

            event = await create_employment_event(
                db,
                company_id=company_id,
                contract_id=contract_id,
                created_by=actor_id,
                data=EmploymentEventCreate(
                    event_type="termination",
                    effective_date=date(2026, 10, 21),
                    order_number="REAL-SEV-2026-10",
                    order_date=date(2026, 10, 21),
                    reason="Documented isolated redundancy",
                    request_key="real-r2-termination-event",
                ),
            )
            event.request_payload = {
                **(event.request_payload or {}),
                "termination_basis": {
                    "ground": "redundancy",
                    "labour_code_provision": "40.1",
                    "source_reference": "Real isolated E2E redundancy order",
                },
            }
            await db.flush()

            settlement_period = PayrollPeriod(
                company_id=company_id,
                year=2026,
                month=10,
                start_date=date(2026, 10, 1),
                end_date=date(2026, 10, 31),
                status="draft",
                created_by=actor_id,
            )
            db.add(settlement_period)
            await db.flush()

            settlement_input = PayrollInput(
                company_id=company_id,
                payroll_period_id=settlement_period.id,
                employment_contract_id=contract_id,
                monthly_norm_minutes=None,
                scheduled_minutes=0,
                worked_minutes=0,
                leave_days=0,
                sick_days=0,
                manual_adjustment_amount=Decimal("0.00"),
                source="manual",
                created_by=actor_id,
            )
            db.add(settlement_input)
            await db.flush()

            # R1 must use the ORIGINAL September payroll.
            initial = await create_initial_verified_severance_source(
                db,
                company_id=company_id,
                employment_contract_id=contract_id,
                payroll_input_id=settlement_input.id,
                request_key="real-r2-initial",
                created_by=actor_id,
            )

            assert initial.revision == 1
            assert initial.supersedes_id is None
            assert initial.gross_amount > 0

            original_r1 = (
                initial.id,
                initial.revision,
                initial.gross_amount,
                initial.base_amount,
                initial.basis_reference,
                initial.request_key,
            )

            print("GENUINE SEVERANCE R1 BEFORE CORRECTION = PASS")

            evidence = await (
                capture_payroll_correction_source(
                    db,
                    company_id=company_id,
                    original_payroll_calculation_id=original.id,
                    request_key="two-month-real-september-r2",
                    reason="Documented full-time norm correction",
                )
            )

            corrected, correction_link = await (
                recalculate_minimum_wage_correction(
                    db,
                    company_id=company_id,
                    original_payroll_calculation_id=original.id,
                    calculated_by=actor_id,
                    request_key="two-month-real-september-r2",
                    reason="Documented full-time norm correction",
                    expected_source_fingerprint_sha256=(
                        evidence["source_fingerprint_sha256"]
                    ),
                    corrected_minimum_wage_context={
                        "full_time_norm_minutes": 9600,
                        "norm_reference": "Calendar corrected",
                    },
                )
            )

            assert original.revision == 1
            assert corrected.revision == 2
            assert corrected.gross_amount != original.gross_amount
            assert (
                correction_link.replacement_payroll_calculation_id
                == corrected.id
            )


            # Both months need genuinely generated historical slices.
            for payroll_input, calculation in (
                (september_input, corrected),
                (august_input, august),
            ):
                slices = list((
                    await db.scalars(
                        select(PayrollInputSalarySlice).where(
                            PayrollInputSalarySlice.company_id
                            == company_id,
                            PayrollInputSalarySlice.payroll_input_id
                            == payroll_input.id,
                        )
                    )
                ).all())
                assert slices

                lines = list((
                    await db.scalars(
                        select(PayrollCalculationLine).where(
                            PayrollCalculationLine.company_id
                            == company_id,
                            PayrollCalculationLine.payroll_calculation_id
                            == calculation.id,
                        )
                    )
                ).all())
                assert lines

                candidate = await derive_verified_earnings_candidate(
                    db,
                    company_id=company_id,
                    payroll_calculation_id=calculation.id,
                )

                assert candidate.minimum_wage_amount > 0
                assert (
                    candidate.salary_amount
                    + candidate.minimum_wage_amount
                    == candidate.gross_amount
                    == calculation.gross_amount
                )
                assert candidate.financial_execution_allowed is False

            result = await derive_database_severance_average(
                db,
                company_id=company_id,
                employment_contract_id=contract_id,
                termination_date=date(2026, 10, 21),
            )

            assert result.reference_start == date(2026, 8, 1)
            assert result.reference_end == date(2026, 9, 30)
            assert result.average_monthly > 0
            assert "revision 2" in result.source_reference

            correction_evidence = CorrectionEvidence(
                reference="REAL-CORRECTED-NORM-2026-12-15",
                effective_date=date(2026, 12, 15),
            )

            revised = await create_verified_severance_correction(
                db,
                company_id=company_id,
                employment_contract_id=contract_id,
                payroll_input_id=settlement_input.id,
                previous_source_id=initial.id,
                request_key="real-r2-severance-correction",
                created_by=actor_id,
                evidence=correction_evidence,
            )

            assert revised.revision == 2
            assert revised.supersedes_id == initial.id
            assert revised.base_amount == result.average_monthly
            assert revised.gross_amount != initial.gross_amount
            assert revised.basis_reference != initial.basis_reference

            await db.refresh(initial)

            assert (
                initial.id,
                initial.revision,
                initial.gross_amount,
                initial.base_amount,
                initial.basis_reference,
                initial.request_key,
            ) == original_r1

            versions = list((
                await db.scalars(
                    select(PayrollTerminationSeverance).where(
                        PayrollTerminationSeverance.company_id
                        == company_id,
                        PayrollTerminationSeverance.termination_event_id
                        == event.id,
                    ).order_by(PayrollTerminationSeverance.revision)
                )
            ).all())

            assert len(versions) == 2
            assert [item.revision for item in versions] == [1, 2]
            assert versions[0].id == initial.id
            assert versions[1].id == revised.id


            # The original previous_source_id and request_key are
            # replayed exactly, after the genuine payroll correction.
            replayed = await create_verified_severance_correction(
                db,
                company_id=company_id,
                employment_contract_id=contract_id,
                payroll_input_id=settlement_input.id,
                previous_source_id=initial.id,
                request_key="real-r2-severance-correction",
                created_by=actor_id,
                evidence=correction_evidence,
            )

            assert replayed.id == revised.id
            assert replayed.revision == 2
            assert replayed.gross_amount == revised.gross_amount
            assert replayed.basis_reference == revised.basis_reference

            # Same key but changed documentary evidence must fail.
            with pytest.raises(
                VerifiedSeveranceSourceError,
                match="Existing correction request requires exact replay",
            ):
                await create_verified_severance_correction(
                    db,
                    company_id=company_id,
                    employment_contract_id=contract_id,
                    payroll_input_id=settlement_input.id,
                    previous_source_id=initial.id,
                    request_key="real-r2-severance-correction",
                    created_by=actor_id,
                    evidence=CorrectionEvidence(
                        reference="DIFFERENT-DOCUMENT",
                        effective_date=date(2026, 12, 15),
                    ),
                )

            # The same key cannot be reused with the new R2
            # presented as its own previous source.
            with pytest.raises(
                VerifiedSeveranceSourceError,
                match="Existing correction request requires exact replay",
            ):
                await create_verified_severance_correction(
                    db,
                    company_id=company_id,
                    employment_contract_id=contract_id,
                    payroll_input_id=settlement_input.id,
                    previous_source_id=revised.id,
                    request_key="real-r2-severance-correction",
                    created_by=actor_id,
                    evidence=correction_evidence,
                )

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

            assert len(replay_rows) == 2

            print("EXACT R2 IDEMPOTENT REPLAY = PASS")
            print("EXACT R2 ID UNCHANGED = PASS")
            print("CHANGED CORRECTION EVIDENCE = BLOCKED")
            print("WRONG REPLAY PREVIOUS SOURCE = BLOCKED")
            print("REPLAY SOURCE ROW COUNT = 2")

            # A second branch from the old source must not be created.
            with pytest.raises(
                VerifiedSeveranceSourceError,
                match="latest source",
            ):
                await create_verified_severance_correction(
                    db,
                    company_id=company_id,
                    employment_contract_id=contract_id,
                    payroll_input_id=settlement_input.id,
                    previous_source_id=initial.id,
                    request_key="real-r2-forbidden-branch",
                    created_by=actor_id,
                    evidence=correction_evidence,
                )

            # Existing R2 request is rejected rather than silently
            # creating a duplicate. Exact R2 replay is a separate gate.
            with pytest.raises(
                VerifiedSeveranceSourceError,
                match="Existing correction request requires exact replay",
            ):
                await create_verified_severance_correction(
                    db,
                    company_id=company_id,
                    employment_contract_id=contract_id,
                    payroll_input_id=settlement_input.id,
                    previous_source_id=revised.id,
                    request_key="real-r2-severance-correction",
                    created_by=actor_id,
                    evidence=correction_evidence,
                )

            assert len(list((
                await db.scalars(
                    select(PayrollTerminationSeverance.id).where(
                        PayrollTerminationSeverance.company_id
                        == company_id,
                        PayrollTerminationSeverance.termination_event_id
                        == event.id,
                    )
                )
            ).all())) == 2

            print("GENUINE SEVERANCE R2 = PASS")
            print("REVISION 1 IMMUTABLE = PASS")
            print("REAL CORRECTED AMOUNT CHANGED = PASS")
            print("SEVERANCE BRANCH = BLOCKED")
            print("DUPLICATE R2 REQUEST = BLOCKED")
            print("SEVERANCE CHAIN TWO ROWS = PASS")


            print("TWO REAL FINALIZED PAYROLL MONTHS = PASS")
            print("REAL SEPTEMBER PAYROLL CORRECTION R2 = PASS")
            print("AUGUST REAL PAYROLL CALCULATION = PASS")
            print("BOTH MONTHS HISTORICAL SALARY SLICES = PASS")
            print("BOTH MONTHS VERIFIED MINIMUM WAGE = PASS")
            print("ORDER100 TWO-MONTH AVERAGE = PASS")
            print("FINANCIAL EXECUTION = BLOCKED")

            # Both revisions must exist before transaction rollback.
            before_rollback = list((
                await db.scalars(
                    select(PayrollTerminationSeverance.id).where(
                        PayrollTerminationSeverance.company_id
                        == company_id,
                        PayrollTerminationSeverance.id.in_(
                            [initial.id, revised.id]
                        ),
                    )
                )
            ).all())
            assert set(before_rollback) == {initial.id, revised.id}

            rollback_identity = {
                "company_id": company_id,
                "severance_ids": (initial.id, revised.id),
                "calculation_ids": (
                    original.id,
                    corrected.id,
                    august.id,
                ),
                "termination_event_id": event.id,
            }

            print("GENUINE R1 R2 EXIST BEFORE ROLLBACK = PASS")

        finally:
            await db.rollback()

    # This is a NEW PostgreSQL session after rollback. It must not
    # see any of the records created during the test transaction.
    from app.models.employment_event import EmploymentEvent

    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as verify_db:
        severance_leaks = list((
            await verify_db.scalars(
                select(PayrollTerminationSeverance.id).where(
                    PayrollTerminationSeverance.company_id
                    == rollback_identity["company_id"],
                    PayrollTerminationSeverance.id.in_(
                        rollback_identity["severance_ids"]
                    ),
                )
            )
        ).all())

        calculation_leaks = list((
            await verify_db.scalars(
                select(PayrollCalculation.id).where(
                    PayrollCalculation.company_id
                    == rollback_identity["company_id"],
                    PayrollCalculation.id.in_(
                        rollback_identity["calculation_ids"]
                    ),
                )
            )
        ).all())

        event_leaks = list((
            await verify_db.scalars(
                select(EmploymentEvent.id).where(
                    EmploymentEvent.company_id
                    == rollback_identity["company_id"],
                    EmploymentEvent.id
                    == rollback_identity["termination_event_id"],
                )
            )
        ).all())

        assert severance_leaks == []
        assert calculation_leaks == []
        assert event_leaks == []

        print("GENUINE R1 R2 POST-ROLLBACK ZERO-LEAK = PASS")
        print("PAYROLL CALCULATION POST-ROLLBACK ZERO-LEAK = PASS")
        print("TERMINATION EVENT POST-ROLLBACK ZERO-LEAK = PASS")
        print("INDEPENDENT POSTGRESQL SESSION VERIFICATION = PASS")
