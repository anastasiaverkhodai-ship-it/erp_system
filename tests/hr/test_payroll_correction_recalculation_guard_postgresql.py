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
async def test_recalculation_rejects_incomplete_evidence_postgresql():
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

            from app.services.payroll_correction_recalculation_service import (
                recalculate_minimum_wage_correction,
            )
            from app.services.payroll_correction_recalculation_contract import (
                PayrollCorrectionRecalculationError,
            )
            from app.services.payroll_correction_source_evidence_service import (
                capture_payroll_correction_source,
            )

            request_key = f"13-8-e3-guard-{original.id}"
            reason = "Documented minimum-wage correction test"

            evidence = await capture_payroll_correction_source(
                db,
                company_id=company.id,
                original_payroll_calculation_id=original.id,
                request_key=request_key,
                reason=reason,
            )

            assert evidence["source_evidence_complete"] is False
            assert evidence["automatic_revision_creation_allowed"] is False

            correct_fingerprint = evidence[
                "source_fingerprint_sha256"
            ]

            async def recalculate(fingerprint):
                return await recalculate_minimum_wage_correction(
                    db,
                    company_id=company.id,
                    original_payroll_calculation_id=original.id,
                    calculated_by=actor.id,
                    request_key=request_key,
                    reason=reason,
                    corrected_minimum_wage_context={
                        "full_time_norm_minutes": 10000,
                        "norm_reference": "Document E3-001",
                    },
                    expected_source_fingerprint_sha256=fingerprint,
                )

            with pytest.raises(
                PayrollCorrectionRecalculationError,
                match="source changed",
            ):
                await recalculate("0" * 64)

            print("STALE SHA256 = REJECTED")

            with pytest.raises(
                PayrollCorrectionRecalculationError,
                match="evidence is incomplete",
            ):
                await recalculate(correct_fingerprint)

            print("INCOMPLETE PROVENANCE = REJECTED")

            calculations = await db.scalar(
                select(func.count(PayrollCalculation.id)).where(
                    PayrollCalculation.company_id == company.id,
                    PayrollCalculation.payroll_input_id
                    == payroll_input.id,
                )
            )

            corrections = await db.scalar(
                select(func.count(PayrollCorrection.id)).where(
                    PayrollCorrection.company_id == company.id,
                )
            )

            assert calculations == 1
            assert corrections == 0

            await db.refresh(original)

            assert original.revision == 1
            assert original.gross_amount == Decimal("10000.00")

            print("ORIGINAL REVISION = UNCHANGED")
            print("CORRECTION LINKS CREATED = 0")
            print("NEW REVISIONS CREATED = 0")

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
            print("FAIL-CLOSED CORRECTION GATE = PASS")

    finally:
        await engine.dispose()
