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
async def test_correction_provenance_integration_postgresql():
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


            from app.services.payroll_correction_source_evidence_service import (
                capture_payroll_correction_source,
            )

            from app.services.payroll_correction_provenance_integration_service import (
                PayrollProvenanceIntegrationError,
                verify_payroll_correction_provenance,
            )

            request_key = (
                f"integrated-source-proof-{original.id}"
            )

            reason = (
                "Verify complete payroll correction source consistency"
            )

            async def capture():
                return await capture_payroll_correction_source(
                    db,
                    company_id=company.id,
                    original_payroll_calculation_id=original.id,
                    request_key=request_key,
                    reason=reason,
                )

            initial = await capture()

            fingerprint = initial["source_fingerprint_sha256"]

            assert isinstance(fingerprint, str)
            assert len(fingerprint) == 64

            async def verify(expected=fingerprint):
                return await verify_payroll_correction_provenance(
                    db,
                    company_id=company.id,
                    original_payroll_calculation_id=original.id,
                    request_key=request_key,
                    reason=reason,
                    expected_source_fingerprint_sha256=expected,
                )

            first = await verify()
            second = await verify()

            assert first == second

            assert first["fingerprint_verified"] is True

            assert (
                first["historical_salary_rate_references_verified"]
                is True
            )

            assert (
                first["additional_source_values_reconciled"]
                is True
            )

            assert (
                first["source_fingerprint_sha256"]
                == fingerprint
            )

            assert first["source_evidence_complete"] is False

            assert (
                first["automatic_revision_creation_allowed"]
                is False
            )

            assert first["financial_execution_allowed"] is False

            assert first["blockers"]

            print("INTEGRATED SOURCE PROVENANCE = PASS")
            print("SHA256 RECHECK = PASS")
            print("HISTORICAL SALARY REFERENCES = PASS")
            print("ADDITIONAL SOURCE CONSISTENCY = PASS")
            print("PROVENANCE COVERAGE = PASS")
            print("DETERMINISTIC REPEAT = PASS")
            print("SOURCE EVIDENCE COMPLETE = FALSE")

            with pytest.raises(
                PayrollProvenanceIntegrationError,
                match="source changed",
            ):
                await verify("0" * 64)

            print("STALE SHA256 = REJECTED")

            original_rate_amount = rate.amount

            # The historical rate is referenced by payroll.
            # PostgreSQL must reject an UPDATE to that rate.
            from sqlalchemy.exc import IntegrityError as _IntegrityError

            with pytest.raises(
                _IntegrityError,
                match="Historical payroll source is immutable",
            ):
                async with db.begin_nested():
                    await db.execute(
                        EmployeeSalaryRate.__table__.update()
                        .where(
                            EmployeeSalaryRate.__table__.c.id == rate.id,
                            EmployeeSalaryRate.__table__.c.company_id
                            == company.id,
                        )
                        .values(
                            amount=original_rate_amount + Decimal("1.00")
                        )
                    )

            await db.refresh(rate)
            assert rate.amount == original_rate_amount

            unchanged_snapshot = await capture()
            assert (
                unchanged_snapshot["source_fingerprint_sha256"]
                == fingerprint
            )
            assert await verify() == first

            print("HISTORICAL RATE UPDATE = BLOCKED BY POSTGRESQL")
            print("HISTORICAL RATE VALUE = UNCHANGED")
            print("SOURCE SHA256 AFTER REJECTION = UNCHANGED")

            original_slice_amount = salary_slice.salary_rate_amount

            # Salary slices already used in payroll are immutable.
            # The failed UPDATE is rolled back to a SAVEPOINT.
            with pytest.raises(
                _IntegrityError,
                match="Historical payroll source is immutable",
            ):
                async with db.begin_nested():
                    await db.execute(
                        PayrollInputSalarySlice.__table__.update()
                        .where(
                            PayrollInputSalarySlice.__table__.c.id
                            == salary_slice.id,
                            PayrollInputSalarySlice.__table__.c.company_id
                            == company.id,
                        )
                        .values(
                            salary_rate_amount=(
                                original_slice_amount + Decimal("1.00")
                            )
                        )
                    )

            await db.refresh(salary_slice)

            assert (
                salary_slice.salary_rate_amount
                == original_slice_amount
            )

            assert await verify() == first

            print("SALARY SLICE UPDATE = BLOCKED BY POSTGRESQL")
            print("SALARY SLICE VALUE = UNCHANGED")
            print("SOURCE PROVENANCE AFTER SAVEPOINT = PASS")

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
            assert original.gross_amount == Decimal("10000.00")

            print("ORIGINAL REVISION = 1")
            print("ORIGINAL GROSS = UNCHANGED")
            print("NEW REVISIONS = 0")
            print("CORRECTION LINKS = 0")
            print("AUTOMATIC REVISION CREATION = BLOCKED")
            print("FINANCIAL EXECUTION = BLOCKED")

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
            print("INTEGRATED PROVENANCE POSTGRESQL E2E = PASS")

    finally:
        await engine.dispose()
