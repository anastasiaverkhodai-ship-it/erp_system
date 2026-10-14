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
async def test_populated_supplement_source_proof_postgresql():
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

            # Exact fields confirmed by the existing ERP model.
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
                        "gross_amount": Decimal("10000.00"),
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
                source_effective_from=date(2096, 4, 1),
                source_effective_to=date(2096, 4, 30),
            )
            db.add(line)
            await db.flush()


            from app.models.payroll_supplement import PayrollSupplement

            from app.services.payroll_correction_additional_source_proof import (
                PayrollAdditionalSourceProofError,
                verify_additional_payroll_sources,
            )

            supplement = PayrollSupplement(
                company_id=company.id,
                payroll_input_id=payroll_input.id,
                kind="bonus",
                work_date=date(2096, 4, 15),
                minutes=0,
                coefficient=Decimal("0.000000"),
                amount=Decimal("250.00"),
                source_reference="Documented bonus E3-B3-C3-B2",
                request_key=f"source-proof-bonus-{payroll_input.id}",
                request_fingerprint="a" * 64,
                created_by=actor.id,
            )

            db.add(supplement)
            await db.flush()

            original.gross_amount = Decimal("10250.00")

            bonus_line = PayrollCalculationLine(
                company_id=company.id,
                payroll_calculation_id=original.id,
                line_no=2,
                line_type="supplement",
                description=(
                    "bonus: Documented bonus E3-B3-C3-B2"
                ),
                quantity=Decimal("1.0000"),
                rate=Decimal("250.0000"),
                amount=Decimal("250.00"),
                currency_code="UAH",
                salary_rate_type="hourly",
                source_salary_rate_id=rate.id,
                source_supplement_id=supplement.id,
                source_effective_from=date(2096, 4, 15),
                source_effective_to=date(2096, 4, 15),
            )

            db.add(bonus_line)
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

            supplement_result = first["components"]["supplement"]

            assert supplement_result["stored_line_count"] == 1
            assert supplement_result["derived_line_count"] == 1
            assert supplement_result["source_values_reconciled"] is True

            assert (
                first["components"]["vacation_pay"]["stored_line_count"]
                == 0
            )

            assert (
                first["components"]["sick_pay"]["stored_line_count"]
                == 0
            )

            assert first["current_source_values_reconciled"] is True
            assert first["historical_source_immutability_verified"] is False
            assert first["complete_payroll_provenance_verified"] is False
            assert first["automatic_revision_creation_allowed"] is False
            assert first["financial_execution_allowed"] is False

            print("POPULATED BONUS SUPPLEMENT = PASS")
            print("BONUS AMOUNT = 250.00 UAH")
            print("SOURCE REFERENCE = VERIFIED")
            print("ORIGINAL BONUS LINE = VERIFIED")
            print("DETERMINISTIC REPEAT = PASS")

            supplement.amount = Decimal("350.00")
            await db.flush()

            with pytest.raises(
                PayrollAdditionalSourceProofError,
                match="provenance mismatch",
            ):
                await verify()

            print("ALTERED BONUS AMOUNT = REJECTED")

            supplement.amount = Decimal("250.00")
            await db.flush()

            restored = await verify()

            assert restored == first

            print("RESTORED BONUS SOURCE = PASS")

            bonus_line.amount = Decimal("999.00")
            await db.flush()

            with pytest.raises(
                PayrollAdditionalSourceProofError,
                match="provenance mismatch",
            ):
                await verify()

            print("ALTERED STORED BONUS LINE = REJECTED")

            bonus_line.amount = Decimal("250.00")
            await db.flush()

            assert await verify() == first

            print("RESTORED STORED BONUS LINE = PASS")

            calculation_count = await db.scalar(
                select(func.count(PayrollCalculation.id)).where(
                    PayrollCalculation.company_id == company.id,
                    PayrollCalculation.payroll_input_id
                    == payroll_input.id,
                )
            )

            correction_count = await db.scalar(
                select(func.count(PayrollCorrection.id)).where(
                    PayrollCorrection.company_id == company.id,
                )
            )

            assert calculation_count == 1
            assert correction_count == 0

            await db.refresh(original)

            assert original.revision == 1
            assert original.gross_amount == Decimal("10250.00")

            print("ORIGINAL CALCULATION = UNCHANGED")
            print("NEW REVISIONS = 0")
            print("CORRECTION LINKS = 0")
            print("AUTOMATIC REVISION CREATION = BLOCKED")

            original_id = original.id

            await db.rollback()

            leaked = await db.scalar(
                select(func.count(PayrollCalculation.id)).where(
                    PayrollCalculation.company_id == company.id,
                    PayrollCalculation.id == original_id,
                )
            )

            assert leaked == 0

            print("ROLLBACK ZERO-LEAK = PASS")
            print("POPULATED SUPPLEMENT POSTGRESQL E2E = PASS")

    finally:
        await engine.dispose()
