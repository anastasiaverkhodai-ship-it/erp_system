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
async def test_real_two_month_corrected_minimum_wage_average(
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

            # October: create genuine period, attendance, payroll input
            # and salary snapshots using normal ERP services.
            contract_id = september_input.employment_contract_id

            october_period = await wage_fixture.create_payroll_period(
                db,
                company_id=company_id,
                created_by=actor_id,
                data=wage_fixture.PayrollPeriodCreate(
                    year=2026,
                    month=10,
                    start_date=date(2026, 10, 1),
                    end_date=date(2026, 10, 31),
                ),
            )

            for day in range(1, 32):
                work_date = date(2026, 10, day)
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
                            2026, 10, day, 9,
                            tzinfo=ZoneInfo("Europe/Kyiv"),
                        ),
                        actual_end_at=datetime(
                            2026, 10, day, 13,
                            tzinfo=ZoneInfo("Europe/Kyiv"),
                        ),
                    ),
                )

            october_input = await wage_fixture.create_payroll_input(
                db,
                company_id=company_id,
                payroll_period_id=october_period.id,
                created_by=actor_id,
                data=wage_fixture.PayrollInputCreate(
                    employment_contract_id=contract_id,
                    manual_adjustment_amount=Decimal("0.00"),
                ),
            )

            await wage_fixture.finalize_payroll_period(
                db,
                company_id=company_id,
                payroll_period_id=october_period.id,
                finalized_by=actor_id,
            )

            october = await calculate_payroll_input(
                db,
                company_id=company_id,
                payroll_input_id=october_input.id,
                calculated_by=actor_id,
                minimum_wage_context={
                    "full_time_norm_minutes": 10560,
                    "norm_reference": "October documented calendar",
                },
            )

            assert october.revision == 1

            # Both months need genuinely generated historical slices.
            for payroll_input, calculation in (
                (september_input, corrected),
                (october_input, october),
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
                termination_date=date(2026, 11, 30),
            )

            assert result.reference_start == date(2026, 9, 1)
            assert result.reference_end == date(2026, 10, 31)
            assert result.average_monthly > 0
            assert "revision 2" in result.source_reference

            print("TWO REAL FINALIZED PAYROLL MONTHS = PASS")
            print("REAL SEPTEMBER PAYROLL CORRECTION R2 = PASS")
            print("OCTOBER REAL PAYROLL CALCULATION = PASS")
            print("BOTH MONTHS HISTORICAL SALARY SLICES = PASS")
            print("BOTH MONTHS VERIFIED MINIMUM WAGE = PASS")
            print("ORDER100 TWO-MONTH AVERAGE = PASS")
            print("FINANCIAL EXECUTION = BLOCKED")

        finally:
            await db.rollback()
