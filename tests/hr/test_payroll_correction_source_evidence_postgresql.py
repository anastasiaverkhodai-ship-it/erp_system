"""Real PostgreSQL fail-closed source evidence acceptance."""

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

from app.models.payroll import (
    PayrollCalculation,
    PayrollInput,
    PayrollInputSalarySlice,
    PayrollPeriod,
    PayrollPeriodStatus,
)

from app.models.payroll_correction import PayrollCorrection

from app.services.payroll_correction_source_evidence_service import (
    capture_payroll_correction_source,
    PayrollCorrectionEvidenceError,
)

from app.services.payroll_correction_preflight_service import (
    PayrollCorrectionPreflightNotFound,
)

from tests.hr.test_payroll_correction_postgresql import (
    _required_values,
    _seed_identity,
)


def _isolated_url():
    assert os.environ.get("RUN_POSTGRES_E2E") == "1", (
        "STOP: isolated PostgreSQL runner required"
    )

    url = make_url(str(settings.database_url))

    assert url.get_backend_name() == "postgresql"

    assert (url.database or "").startswith("erp_r3_copy_"), (
        "STOP: main database access forbidden"
    )

    return url.set(drivername="postgresql+asyncpg")


@pytest.mark.asyncio
async def test_source_evidence_rejects_incomplete_postgresql_sources():
    url = _isolated_url()

    engine = create_async_engine(
        url,
        echo=False,
    )

    Session = async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    try:
        async with Session() as db:

            database_name = await db.scalar(
                text("SELECT current_database()")
            )

            assert database_name == url.database
            assert database_name.startswith("erp_r3_copy_")

            print("ISOLATED DATABASE GUARD = PASS")

            company, actor, employee, contract = (
                await _seed_identity(db)
            )

            company_id = company.id
            actor_id = actor.id

            period = PayrollPeriod(
                **_required_values(
                    PayrollPeriod,
                    {
                        "company_id": company_id,
                        "year": 2096,
                        "month": 4,
                        "start_date": date(2096, 4, 1),
                        "end_date": date(2096, 4, 30),
                        "status": PayrollPeriodStatus.FINALIZED,
                        "finalized_at": datetime.now(UTC),
                        "finalized_by": actor_id,
                        "created_by": actor_id,
                    },
                )
            )

            db.add(period)
            await db.flush()

            payroll_input = PayrollInput(
                **_required_values(
                    PayrollInput,
                    {
                        "company_id": company_id,
                        "payroll_period_id": period.id,
                        "employment_contract_id": contract.id,
                        "created_by": actor_id,
                    },
                )
            )

            db.add(payroll_input)
            await db.flush()

            original = PayrollCalculation(
                **_required_values(
                    PayrollCalculation,
                    {
                        "company_id": company_id,
                        "payroll_period_id": period.id,
                        "payroll_input_id": payroll_input.id,
                        "employment_contract_id": contract.id,
                        "revision": 1,
                        "status": "calculated",
                        "currency_code": "UAH",
                        "gross_amount": Decimal("10000.00"),
                        "calculated_by": actor_id,
                    },
                )
            )

            db.add(original)
            await db.flush()

            original_id = original.id
            input_id = payroll_input.id

            async def capture(
                *,
                calculation_id=original_id,
                key=None,
            ):
                return await capture_payroll_correction_source(
                    db,
                    company_id=company_id,
                    original_payroll_calculation_id=(
                        calculation_id
                    ),
                    request_key=(
                        key
                        if key is not None
                        else f"13-8-evidence-{original_id}"
                    ),
                    reason="Verified correction evidence E2E",
                )

            with pytest.raises(
                PayrollCorrectionPreflightNotFound
            ):
                await capture(
                    calculation_id=2147483647
                )

            print("MISSING CALCULATION = REJECTED")

            with pytest.raises(
                PayrollCorrectionEvidenceError,
                match="salary slices",
            ):
                await capture()

            print("MISSING SALARY SLICES = REJECTED")

            with pytest.raises(
                PayrollCorrectionEvidenceError,
                match="salary slices",
            ):
                await capture()

            print("REPEATED INCOMPLETE SOURCE = REJECTED")

            calculations = await db.scalar(
                select(func.count(PayrollCalculation.id)).where(
                    PayrollCalculation.company_id == company_id,
                    PayrollCalculation.payroll_input_id == input_id,
                )
            )

            corrections = await db.scalar(
                select(func.count(PayrollCorrection.id)).where(
                    PayrollCorrection.company_id == company_id,
                )
            )

            salary_slices = await db.scalar(
                select(func.count(PayrollInputSalarySlice.id)).where(
                    PayrollInputSalarySlice.company_id == company_id,
                    PayrollInputSalarySlice.payroll_input_id == input_id,
                )
            )

            assert calculations == 1
            assert corrections == 0
            assert salary_slices == 0

            await db.refresh(original)

            assert original.revision == 1
            assert original.gross_amount == Decimal("10000.00")
            assert original.currency_code == "UAH"

            print("ORIGINAL CALCULATION = UNCHANGED")
            print("CORRECTION LINKS CREATED = 0")
            print("NEW REVISIONS CREATED = 0")
            print("FINANCIAL EXECUTION = NONE")
            print("SOURCE EVIDENCE FAIL-CLOSED = PASS")

            await db.rollback()

            leaked_calculation = await db.scalar(
                select(func.count(PayrollCalculation.id)).where(
                    PayrollCalculation.id == original_id,
                    PayrollCalculation.company_id == company_id,
                )
            )

            leaked_correction = await db.scalar(
                select(func.count(PayrollCorrection.id)).where(
                    PayrollCorrection.company_id == company_id,
                )
            )

            assert leaked_calculation == 0
            assert leaked_correction == 0

            print("ROLLBACK ZERO-LEAK = PASS")

    finally:
        await engine.dispose()
