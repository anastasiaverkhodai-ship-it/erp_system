"""Positive PostgreSQL test for payroll correction source evidence."""

import os
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, func, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.models.employee_salary_rate import EmployeeSalaryRate
from app.models.payroll import (
    PayrollPeriod,
    PayrollPeriodStatus,
    PayrollInput,
    PayrollInputSalarySlice,
    PayrollCalculation,
    PayrollCalculationLine,
)
from app.models.payroll_correction import PayrollCorrection
from app.services.payroll_correction_source_evidence_service import (
    capture_payroll_correction_source,
)
from tests.hr.test_payroll_correction_postgresql import (
    _required_values,
    _seed_identity,
)


@pytest.mark.asyncio
async def test_saved_minimum_wage_assessment_postgresql():
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
                        "year": 2026,
                        "month": 4,
                        "start_date": date(2026, 4, 1),
                        "end_date": date(2026, 4, 30),
                        "status": PayrollPeriodStatus.FINALIZED,
                        "finalized_at": datetime.now(UTC),
                        "finalized_by": actor.id,
                        "created_by": actor.id,
                    },
                )
            )
            db.add(period)
            await db.flush()

            # Exact fields confirmed by the existing ERP model.
            rate = EmployeeSalaryRate(
                company_id=company.id,
                employment_contract_id=contract.id,
                rate_type="hourly",
                amount=Decimal("100.00"),
                currency_code="UAH",
                effective_from=date(2026, 4, 1),
                effective_to=date(2026, 4, 30),
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
                effective_from=date(2026, 4, 1),
                effective_to=date(2026, 4, 30),
                scheduled_minutes=6000,
                worked_minutes=6000,
            )
            db.add(salary_slice)
            await db.flush()

            from app.services.payroll_minimum_wage_service import (
                derive_minimum_wage_assessment,
            )

            original_salary_lines = [
                {
                    "line_type": "salary",
                    "currency_code": "UAH",
                    "amount": Decimal("10000.00"),
                    "source_salary_rate_id": rate.id,
                }
            ]

            assessment, minimum_line = (
                await derive_minimum_wage_assessment(
                    db,
                    company_id=company.id,
                    period=period,
                    payroll_input=payroll_input,
                    salary_slices=[salary_slice],
                    lines=original_salary_lines,
                    context={},
                )
            )

            assert assessment["status"] == "assessed"
            assert assessment["rule_version"] == (
                "UA-4695-IX-2026-v1"
            )
            assert assessment["rate_type"] == "hourly"
            assert Decimal(
                assessment["minimum_rate"]
            ) == Decimal("52")
            assert Decimal(
                assessment["guaranteed_amount"]
            ) == Decimal("5200.00")
            assert Decimal(
                assessment["included_earnings"]
            ) == Decimal("10000.00")
            assert Decimal(
                assessment["top_up"]
            ) == Decimal("0.00")

            assert minimum_line is None

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
                        "gross_amount": Decimal("10000.00"),
                            "minimum_wage_assessment": assessment,
                        "calculated_by": actor.id,
                    },
                )
            )
            db.add(original)
            await db.flush()

            line = PayrollCalculationLine(
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
                source_effective_from=date(2026, 4, 1),
                source_effective_to=date(2026, 4, 30),
            )
            db.add(line)
            await db.flush()

            from copy import deepcopy

            from app.services.payroll_correction_minimum_wage_proof import (
                PayrollMinimumWageProofError,
                verify_saved_minimum_wage_assessment,
            )

            await db.refresh(original)

            saved_assessment = original.minimum_wage_assessment

            assert isinstance(saved_assessment, dict)
            assert saved_assessment == assessment

            stored_lines = (
                await db.scalars(
                    select(PayrollCalculationLine)
                    .where(
                        PayrollCalculationLine.company_id
                        == company.id,
                        PayrollCalculationLine.payroll_calculation_id
                        == original.id,
                    )
                    .order_by(PayrollCalculationLine.line_no)
                )
            ).all()

            assert len(stored_lines) == 1

            def verify(
                current_assessment=saved_assessment,
                current_lines=None,
                start=None,
                end=None,
            ):
                return verify_saved_minimum_wage_assessment(
                    assessment=current_assessment,
                    calculation_lines=(
                        stored_lines
                        if current_lines is None
                        else current_lines
                    ),
                    period_start=(
                        period.start_date
                        if start is None
                        else start
                    ),
                    period_end=(
                        period.end_date
                        if end is None
                        else end
                    ),
                    expected_salary_rate_type="hourly",
                )

            first = verify()
            second = verify()

            assert first == second
            assert first["saved_assessment_consistent"] is True
            assert first["minimum_wage_line_count"] == 0
            assert first["complete_payroll_provenance_verified"] is False
            assert first["automatic_revision_creation_allowed"] is False
            assert first["financial_execution_allowed"] is False

            print("2026 MINIMUM WAGE DERIVATION = PASS")
            print("SAVED ASSESSMENT = VERIFIED")
            print("HOURLY MINIMUM = 52 UAH")
            print("GUARANTEED AMOUNT = 5200 UAH")
            print("INCLUDED EARNINGS = 10000 UAH")
            print("TOP-UP = 0 UAH")
            print("DETERMINISTIC REPEAT = PASS")

            changed_rule = deepcopy(saved_assessment)
            changed_rule["rule_version"] = "INVALID-RULE"

            with pytest.raises(
                PayrollMinimumWageProofError,
                match="rule version",
            ):
                verify(changed_rule)

            print("ALTERED RULE VERSION = REJECTED")

            changed_rate = deepcopy(saved_assessment)
            changed_rate["minimum_rate"] = "40"

            with pytest.raises(
                PayrollMinimumWageProofError,
                match="minimum rate",
            ):
                verify(changed_rate)

            print("ALTERED MINIMUM RATE = REJECTED")

            changed_amount = deepcopy(saved_assessment)
            changed_amount["top_up"] = "200.00"

            with pytest.raises(
                PayrollMinimumWageProofError,
                match="top-up reconciliation",
            ):
                verify(changed_amount)

            print("ALTERED TOP-UP = REJECTED")

            changed_period = date(2096, 4, 1)

            with pytest.raises(
                PayrollMinimumWageProofError,
                match="outside the verified 2026 rule",
            ):
                verify(start=changed_period)

            print("INVALID RULE PERIOD = REJECTED")

            injected_line = {
                "line_type": "minimum_wage",
                "currency_code": "UAH",
                "quantity": Decimal("1"),
                "rate": Decimal("200.00"),
                "amount": Decimal("200.00"),
                "source_effective_from": period.start_date,
                "source_effective_to": period.end_date,
            }

            with pytest.raises(
                PayrollMinimumWageProofError,
                match="Unexpected minimum-wage top-up line",
            ):
                verify(
                    current_lines=[
                        *stored_lines,
                        injected_line,
                    ],
                )

            print("UNJUSTIFIED TOP-UP LINE = REJECTED")

            assert verify() == first

            print("ORIGINAL ASSESSMENT = UNCHANGED")

            calculations = await db.scalar(
                select(func.count(PayrollCalculation.id))
                .where(
                    PayrollCalculation.company_id == company.id,
                    PayrollCalculation.payroll_input_id
                    == payroll_input.id,
                )
            )

            corrections = await db.scalar(
                select(func.count(PayrollCorrection.id))
                .where(
                    PayrollCorrection.company_id == company.id,
                )
            )

            assert calculations == 1
            assert corrections == 0

            await db.refresh(original)

            assert original.revision == 1
            assert original.gross_amount == Decimal("10000.00")
            assert original.minimum_wage_assessment == assessment

            print("ORIGINAL REVISION = 1")
            print("ORIGINAL GROSS = UNCHANGED")
            print("NEW REVISIONS = 0")
            print("CORRECTION LINKS = 0")

            original_id = original.id

            await db.rollback()

            leaked = await db.scalar(
                select(func.count(PayrollCalculation.id))
                .where(
                    PayrollCalculation.company_id == company.id,
                    PayrollCalculation.id == original_id,
                )
            )

            assert leaked == 0

            print("ROLLBACK ZERO-LEAK = PASS")
            print("REVISION CREATION = BLOCKED")
            print("FINANCIAL EXECUTION = BLOCKED")
            print("MINIMUM WAGE POSTGRESQL E2E = PASS")

    finally:
        await engine.dispose()
