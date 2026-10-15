"""Populated vacation and sick leave source proof on isolated PostgreSQL."""

import os
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.models.employee_salary_rate import EmployeeSalaryRate
from app.models.leave_request import (
    LeaveRequest,
    LeaveRequestStatus,
    LeaveType,
)
from app.models.payroll import (
    PayrollPeriod,
    PayrollPeriodStatus,
    PayrollInput,
    PayrollInputSalarySlice,
    PayrollCalculation,
    PayrollCalculationLine,
)
from app.models.payroll_correction import PayrollCorrection
from app.models.payroll_vacation import PayrollVacationCalculation
from app.models.payroll_sick_leave import PayrollSickLeaveCalculation

from app.services.payroll_correction_additional_source_proof import (
    PayrollAdditionalSourceProofError,
    verify_additional_payroll_sources,
)
from tests.hr.test_payroll_correction_postgresql import (
    _required_values,
    _seed_identity,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["vacation", "sick"])
async def test_populated_leave_source_proof_postgresql(kind):
    assert os.environ.get("RUN_POSTGRES_E2E") == "1"

    url = make_url(str(settings.database_url))
    assert url.get_backend_name() == "postgresql"
    assert (url.database or "").startswith("erp_r3_copy_")

    engine = create_async_engine(
        url.set(drivername="postgresql+asyncpg")
    )
    Session = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    try:
        async with Session() as db:
            assert await db.scalar(
                text("SELECT current_database()")
            ) == url.database

            company, actor, employee, contract = (
                await _seed_identity(db)
            )

            period = PayrollPeriod(
                **_required_values(
                    PayrollPeriod,
                    {
                        "company_id": company.id,
                        "year": 2096,
                        "month": 4,
                        "start_date": date(2096, 4, 1),
                        "end_date": date(2096, 4, 30),
                        "status": PayrollPeriodStatus.FINALIZED,
                        "finalized_at": datetime.now(UTC),
                        "finalized_by": actor.id,
                        "created_by": actor.id,
                    },
                )
            )
            db.add(period)
            await db.flush()

            rate = EmployeeSalaryRate(
                company_id=company.id,
                employment_contract_id=contract.id,
                rate_type="hourly",
                amount=Decimal("100.00"),
                currency_code="UAH",
                effective_from=date(2096, 4, 1),
                effective_to=date(2096, 4, 30),
                created_by=actor.id,
            )
            db.add(rate)
            await db.flush()

            payroll_input = PayrollInput(
                **_required_values(
                    PayrollInput,
                    {
                        "company_id": company.id,
                        "payroll_period_id": period.id,
                        "employment_contract_id": contract.id,
                        "created_by": actor.id,
                        "scheduled_minutes": 6000,
                        "worked_minutes": 6000,
                    },
                )
            )
            db.add(payroll_input)
            await db.flush()

            salary_slice = PayrollInputSalarySlice(
                company_id=company.id,
                payroll_input_id=payroll_input.id,
                salary_rate_id=rate.id,
                salary_rate_type="hourly",
                salary_rate_amount=Decimal("100.00"),
                currency_code="UAH",
                effective_from=date(2096, 4, 1),
                effective_to=date(2096, 4, 30),
                scheduled_minutes=6000,
                worked_minutes=6000,
            )
            db.add(salary_slice)
            await db.flush()

            original = PayrollCalculation(
                **_required_values(
                    PayrollCalculation,
                    {
                        "company_id": company.id,
                        "payroll_period_id": period.id,
                        "payroll_input_id": payroll_input.id,
                        "employment_contract_id": contract.id,
                        "revision": 1,
                        "status": "calculated",
                        "currency_code": "UAH",
                        "gross_amount": Decimal("10000.00") + (Decimal("500.00") if kind == "vacation" else Decimal("300.00")),
                        "calculated_by": actor.id,
                    },
                )
            )
            db.add(original)
            await db.flush()

            db.add(
                PayrollCalculationLine(
                    company_id=company.id,
                    payroll_calculation_id=original.id,
                    line_no=1,
                    line_type="salary",
                    description="Hourly salary",
                    quantity=Decimal("100.0000"),
                    rate=Decimal("100.0000"),
                    amount=Decimal("10000.00"),
                    currency_code="UAH",
                    salary_rate_type="hourly",
                    source_salary_rate_id=rate.id,
                    source_effective_from=date(2096, 4, 1),
                    source_effective_to=date(2096, 4, 30),
                )
            )
            await db.flush()

            leave_type = (
                LeaveType.ANNUAL
                if kind == "vacation"
                else LeaveType.SICK
            )

            leave = LeaveRequest(
                **_required_values(
                    LeaveRequest,
                    {
                        "company_id": company.id,
                        "employment_contract_id": contract.id,
                        "leave_type": leave_type,
                        "status": LeaveRequestStatus.APPROVED,
                        "start_date": date(2096, 4, 15),
                        "end_date": date(2096, 4, 16),
                        "reason": f"Documented {kind} E2E",
                        "approved_by": actor.id,
                        "approved_at": datetime.now(UTC),
                    },
                )
            )
            db.add(leave)
            await db.flush()

            if kind == "vacation":
                amount = Decimal("500.00")
                row = PayrollVacationCalculation(
                    **_required_values(
                        PayrollVacationCalculation,
                        {
                            "company_id": company.id,
                            "leave_request_id": leave.id,
                            "employment_contract_id": contract.id,
                            "leave_days": Decimal("2.00"),
                            "reference_period_start": date(2095, 4, 1),
                            "reference_period_end": date(2096, 3, 31),
                            "eligible_earnings": Decimal("25000.00"),
                            "eligible_days": Decimal("100.00"),
                            "average_daily_amount": Decimal("250.000000"),
                            "vacation_pay_amount": amount,
                            "currency_code": "UAH",
                            "rule_code": "E2E-VERIFIED-VACATION",
                            "rule_version": "1",
                            "calculated_by": actor.id,
                            "calculated_at": datetime.now(UTC),
                        },
                    )
                )
                line_type = "vacation_pay"
                source_field = "source_vacation_calculation_id"
                rate_amount = Decimal("250.0000")
            else:
                amount = Decimal("300.00")
                row = PayrollSickLeaveCalculation(
                    **_required_values(
                        PayrollSickLeaveCalculation,
                        {
                            "company_id": company.id,
                            "leave_request_id": leave.id,
                            "employment_contract_id": contract.id,
                            "benefit_case_code": "E2E-SICK",
                            "sick_days": Decimal("2.00"),
                            "reference_period_start": date(2095, 4, 1),
                            "reference_period_end": date(2096, 3, 31),
                            "eligible_earnings": Decimal("30000.00"),
                            "eligible_days": Decimal("100.00"),
                            "average_daily_amount": Decimal("300.000000"),
                            "insurance_service_months": 120,
                            "benefit_percent": Decimal("50.0000"),
                            "limited_service_rule_applied": False,
                            "daily_benefit_amount": Decimal("150.000000"),
                            "employer_days": Decimal("2.00"),
                            "insurer_days": Decimal("0.00"),
                            "employer_amount": amount,
                            "insurer_amount": Decimal("0.00"),
                            "sick_pay_amount": amount,
                            "currency_code": "UAH",
                            "rule_code": "E2E-VERIFIED-SICK",
                            "rule_version": "1",
                            "calculated_by": actor.id,
                            "calculated_at": datetime.now(UTC),
                        },
                    )
                )
                line_type = "sick_pay"
                source_field = "source_sick_leave_calculation_id"
                rate_amount = Decimal("150.0000")

            db.add(row)
            await db.flush()


            line_values = {
                "company_id": company.id,
                "payroll_calculation_id": original.id,
                "line_no": 2,
                "line_type": line_type,
                "description": f"Documented {kind} E2E",
                "quantity": Decimal("2.0000"),
                "rate": rate_amount,
                "amount": amount,
                "currency_code": "UAH",
                source_field: row.id,
            }

            leave_line = PayrollCalculationLine(**line_values)
            db.add(leave_line)
            await db.flush()

            async def verify():
                return await verify_additional_payroll_sources(
                    db,
                    company_id=company.id,
                    payroll_input=payroll_input,
                    period=period,
                    salary_slices=[salary_slice],
                    original_payroll_calculation_id=original.id,
                )

            first = await verify()
            second = await verify()

            assert first == second

            result = first["components"][line_type]
            assert result["stored_line_count"] == 1
            assert result["derived_line_count"] == 1
            assert result["source_values_reconciled"] is True

            assert first["complete_payroll_provenance_verified"] is False
            assert first["automatic_revision_creation_allowed"] is False
            assert first["financial_execution_allowed"] is False

            print(kind.upper(), "POPULATED SOURCE = PASS")
            print(kind.upper(), "REPEATED VERIFICATION = PASS")

            old_amount = leave_line.amount

            # c026 historical freeze protects calculation lines.
            # Test database-level rejection without changing history.
            from sqlalchemy.exc import IntegrityError as _IntegrityError

            with pytest.raises(
                _IntegrityError,
                match="Historical payroll source is immutable",
            ):
                async with db.begin_nested():
                    await db.execute(
                        text(
                            "UPDATE payroll_calculation_lines "
                            "SET amount = :new_amount "
                            "WHERE company_id = :company_id "
                            "AND id = :line_id"
                        ),
                        {
                            "new_amount": old_amount + Decimal("1.00"),
                            "company_id": company.id,
                            "line_id": leave_line.id,
                        },
                    )

            # The rejected SQL statement has been rolled back
            # to the savepoint. The parent transaction is usable.
            await db.refresh(leave_line)
            assert leave_line.amount == old_amount

            # Historical source proof must still match exactly.
            assert await verify() == first

            print(
                kind.upper(),
                "HISTORICAL LINE UPDATE = BLOCKED BY POSTGRESQL",
            )
            print(
                kind.upper(),
                "SOURCE PROVENANCE = UNCHANGED",
            )

            calculations = await db.scalar(
                select(func.count(PayrollCalculation.id)).where(
                    PayrollCalculation.company_id == company.id,
                    PayrollCalculation.payroll_input_id == payroll_input.id,
                )
            )
            corrections = await db.scalar(
                select(func.count(PayrollCorrection.id)).where(
                    PayrollCorrection.company_id == company.id,
                )
            )

            assert calculations == 1
            assert corrections == 0
            assert original.revision == 1

            print(kind.upper(), "REVISION 1 = UNCHANGED")
            print(kind.upper(), "NEW REVISIONS = 0")
            print(kind.upper(), "CORRECTION LINKS = 0")

            original_id = original.id
            await db.rollback()

            leaked = await db.scalar(
                select(func.count(PayrollCalculation.id)).where(
                    PayrollCalculation.company_id == company.id,
                    PayrollCalculation.id == original_id,
                )
            )
            assert leaked == 0

            print(kind.upper(), "ROLLBACK ZERO-LEAK = PASS")

    finally:
        await engine.dispose()
