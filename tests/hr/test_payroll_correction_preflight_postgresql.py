"""Real PostgreSQL acceptance for payroll correction preflight.

This test MUST run through run_postgresql_regression_copy.py.
It never authorizes payment, accounting posting or salary mutation.
"""

import os

from datetime import date
from decimal import Decimal

import pytest

from sqlalchemy import select, text
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
    PayrollPeriod,
    PayrollPeriodStatus,
)
from app.models.journal_entry import JournalEntry

from app.services.payroll_correction_preflight_service import (
    prepare_payroll_correction,
    PayrollCorrectionPreflightConflict,
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

    assert url.get_backend_name() == "postgresql", (
        "STOP: PostgreSQL required"
    )

    assert (url.database or "").startswith("erp_r3_copy_"), (
        "STOP: main database is forbidden"
    )

    return url.set(drivername="postgresql+asyncpg")


@pytest.mark.asyncio
async def test_preflight_eligibility_and_negative_guards_real_postgresql():
    url = _isolated_url()
    engine = create_async_engine(url, echo=False)

    Session = async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    try:
        async with Session() as db:
            actual_database = await db.scalar(
                text("SELECT current_database()")
            )

            assert actual_database == url.database
            assert actual_database.startswith("erp_r3_copy_")

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
                        "year": 2097,
                        "month": 3,
                        "start_date": date(2097, 3, 1),
                        "end_date": date(2097, 3, 31),
                        "status": PayrollPeriodStatus.DRAFT,
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
                        "revision": 1,
                        "payroll_input_id": payroll_input.id,
                        "employment_contract_id": contract.id,
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
            request_key = f"13-8-preflight-{original_id}"

            async def preflight(
                *,
                key=request_key,
                reason="Verified payroll correction",
                calculation_id=original_id,
            ):
                return await prepare_payroll_correction(
                    db,
                    company_id=company_id,
                    original_payroll_calculation_id=calculation_id,
                    request_key=key,
                    reason=reason,
                )

            with pytest.raises(
                PayrollCorrectionPreflightNotFound
            ):
                await preflight(
                    calculation_id=2147483647
                )

            print("MISSING CALCULATION GUARD = PASS")

            with pytest.raises(
                PayrollCorrectionPreflightConflict,
                match="request_key",
            ):
                await preflight(key=" ")

            print("EMPTY REQUEST KEY GUARD = PASS")

            with pytest.raises(
                PayrollCorrectionPreflightConflict,
                match="100 characters",
            ):
                await preflight(key="x" * 101)

            print("REQUEST KEY LENGTH GUARD = PASS")

            with pytest.raises(
                PayrollCorrectionPreflightConflict,
                match="reason",
            ):
                await preflight(reason=" ")

            print("EMPTY CORRECTION REASON GUARD = PASS")

            with pytest.raises(
                PayrollCorrectionPreflightConflict,
                match="finalized",
            ):
                await preflight()

            print("DRAFT PERIOD GUARD = PASS")

            from datetime import UTC, datetime

            period.status = PayrollPeriodStatus.FINALIZED
            period.finalized_at = datetime.now(UTC)
            period.finalized_by = actor_id
            await db.flush()

            result = await preflight()

            assert result["eligible"] is True
            assert result["company_id"] == company_id
            assert result["original_payroll_calculation_id"] == original_id
            assert result["payroll_input_id"] == payroll_input.id
            assert result["current_revision"] == 1
            assert result["next_revision"] == 2

            assert (
                result["requires_verified_recalculation_source"]
                is True
            )

            assert (
                result["automatic_revision_creation_allowed"]
                is False
            )

            print("VALID PREFLIGHT = PASS")
            print("NEXT REVISION = 2")

            from sqlalchemy import func
            from app.models.payroll_correction import PayrollCorrection

            async def count_calculations():
                return await db.scalar(
                    select(func.count(PayrollCalculation.id)).where(
                        PayrollCalculation.company_id == company_id,
                        PayrollCalculation.payroll_input_id
                        == payroll_input.id,
                    )
                )

            async def count_corrections():
                return await db.scalar(
                    select(func.count(PayrollCorrection.id)).where(
                        PayrollCorrection.company_id == company_id,
                    )
                )

            initial_calculations = await count_calculations()
            initial_corrections = await count_corrections()

            assert initial_calculations == 1
            assert initial_corrections == 0

            original_gross = original.gross_amount
            original_revision = original.revision
            original_currency = original.currency_code
            original_input_id = original.payroll_input_id

            for attempt in range(3):
                repeated = await preflight()

                assert repeated["eligible"] is True
                assert repeated["next_revision"] == 2
                assert (
                    repeated["automatic_revision_creation_allowed"]
                    is False
                )

                assert (
                    await count_calculations()
                    == initial_calculations
                )

                assert (
                    await count_corrections()
                    == initial_corrections
                )

                await db.refresh(original)

                assert original.gross_amount == original_gross
                assert original.revision == original_revision
                assert original.currency_code == original_currency
                assert original.payroll_input_id == original_input_id

                print(
                    "PREFLIGHT REPEAT",
                    attempt + 1,
                    "= NO MUTATION"
                )

            print("ORIGINAL PAYROLL AMOUNT = UNCHANGED")
            print("ORIGINAL PAYROLL REVISION = UNCHANGED")
            print("ORIGINAL PAYROLL INPUT = UNCHANGED")
            print("CORRECTION LINKS CREATED = 0")
            print("NEW PAYROLL CALCULATIONS CREATED = 0")
            print("PREFLIGHT IMMUTABILITY = PASS")
            print("AUTO REVISION CREATION = BLOCKED")

            pending_journal = JournalEntry(
                **_required_values(
                    JournalEntry,
                    {
                        "company_id": company_id,
                        "created_by": actor_id,
                        "entry_date": period.end_date,
                        "payroll_calculation_id": original.id,
                        "status": "draft",
                    },
                )
            )

            db.add(pending_journal)
            await db.flush()

            with pytest.raises(
                PayrollCorrectionPreflightConflict,
                match="journal",
            ):
                await preflight()

            print("ACTIVE JOURNAL GUARD = PASS")

            await db.delete(pending_journal)
            await db.flush()

            # Create the temporary later revision in its
            # own SAVEPOINT so cleanup never deletes history.
            _later_revision_savepoint = await db.begin_nested()

            replacement = PayrollCalculation(
                **_required_values(
                    PayrollCalculation,
                    {
                        "company_id": company_id,
                        "payroll_period_id": period.id,
                        "revision": 2,
                        "payroll_input_id": payroll_input.id,
                        "employment_contract_id": contract.id,
                        "status": "calculated",
                        "currency_code": "UAH",
                        "gross_amount": Decimal("10100.00"),
                        "calculated_by": actor_id,
                    },
                )
            )

            db.add(replacement)
            await db.flush()

            with pytest.raises(
                PayrollCorrectionPreflightConflict,
                match="later payroll revision",
            ):
                await preflight()

            print("UNLINKED LATER REVISION GUARD = PASS")

            # The later revision is intentionally temporary.
            # PostgreSQL historical freeze forbids DELETE.
            # Rolling back the SAVEPOINT undoes its INSERT.
            replacement_id = replacement.id

            await _later_revision_savepoint.rollback()

            assert await count_calculations() == initial_calculations
            assert await count_corrections() == initial_corrections

            removed_revision_count = await db.scalar(
                select(func.count(PayrollCalculation.id)).where(
                    PayrollCalculation.company_id == company_id,
                    PayrollCalculation.id == replacement_id,
                )
            )

            assert removed_revision_count == 0

            print("TEMPORARY LATER REVISION = SAVEPOINT ROLLED BACK")
            print("HISTORICAL DELETE = NOT EXECUTED")
            print("ORIGINAL PAYROLL = PRESERVED")

            result_again = await preflight()

            assert result_again["eligible"] is True
            assert result_again["next_revision"] == 2

            print("PREFLIGHT AFTER CLEANUP = PASS")

            await db.rollback()

            leaked = await db.scalar(
                select(PayrollCalculation.id).where(
                    PayrollCalculation.id == original_id,
                    PayrollCalculation.company_id == company_id,
                )
            )

            assert leaked is None

            print("ROLLBACK ZERO-LEAK = PASS")

    finally:
        await engine.dispose()
