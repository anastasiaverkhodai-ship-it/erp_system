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
async def test_minimum_wage_rederivation_postgresql():
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
                amount=Decimal("40.00"),
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
                salary_rate_amount=Decimal("40.00"),
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
                    "amount": Decimal("4000.00"),
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
            ) == Decimal("4000.00")
            assert Decimal(
                assessment["top_up"]
            ) == Decimal("1200.00")

            assert minimum_line is not None

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
                        "gross_amount": Decimal("5200.00"),
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
                rate=Decimal("40.0000"),
                amount=Decimal("4000.00"),
                currency_code="UAH",
                salary_rate_type="hourly",
                source_salary_rate_id=rate.id,
                source_effective_from=date(2026, 4, 1),
                source_effective_to=date(2026, 4, 30),
            )
            db.add(line)
            await db.flush()



            from app.services.payroll_correction_minimum_wage_rederivation import (
                PayrollMinimumWageRederivationError,
                verify_original_minimum_wage_rederivation,
            )

            assert minimum_line is not None
            assert minimum_line["line_type"] == "minimum_wage"
            assert minimum_line["currency_code"] == "UAH"

            minimum_line["line_no"] = 2

            db.add(
                PayrollCalculationLine(
                    company_id=company.id,
                    payroll_calculation_id=original.id,
                    **minimum_line,
                )
            )

            await db.flush()
            await db.refresh(original)

            stored_lines = (
                await db.scalars(
                    select(PayrollCalculationLine)
                    .where(
                        PayrollCalculationLine.company_id == company.id,
                        PayrollCalculationLine.payroll_calculation_id
                        == original.id,
                    )
                    .order_by(PayrollCalculationLine.line_no)
                )
            ).all()

            assert len(stored_lines) == 2

            salary_line = next(
                item
                for item in stored_lines
                if item.line_type == "salary"
            )

            top_up_line = next(
                item
                for item in stored_lines
                if item.line_type == "minimum_wage"
            )

            assert salary_line.amount == Decimal("4000.00")
            assert top_up_line.amount == Decimal("1200.00")
            assert original.gross_amount == Decimal("5200.00")

            assert (
                salary_line.amount + top_up_line.amount
                == original.gross_amount
            )

            async def verify():
                return await verify_original_minimum_wage_rederivation(
                    db,
                    company_id=company.id,
                    period=period,
                    payroll_input=payroll_input,
                    salary_slices=[salary_slice],
                    original_calculation=original,
                    original_lines=stored_lines,
                )

            first = await verify()
            second = await verify()

            assert first == second

            assert first["saved_assessment_consistent"] is True

            assert (
                first["current_erp_rederivation_reconciled"]
                is True
            )

            assert first["minimum_wage_line_count"] == 1

            assert (
                first["historical_source_immutability_verified"]
                is False
            )

            assert (
                first["complete_payroll_provenance_verified"]
                is False
            )

            assert (
                first["automatic_revision_creation_allowed"]
                is False
            )

            assert first["financial_execution_allowed"] is False

            print("REAL POSTGRESQL RE-DERIVATION = PASS")
            print("RULE PERIOD = APRIL 2026")
            print("ORIGINAL SALARY = 4000 UAH")
            print("GUARANTEED MINIMUM = 5200 UAH")
            print("RE-DERIVED TOP-UP = 1200 UAH")
            print("RECONCILED GROSS = 5200 UAH")
            print("SAVED ASSESSMENT = MATCHED")
            print("SAVED TOP-UP LINE = MATCHED")
            print("DETERMINISTIC REPEAT = PASS")

            # Mutation 1: the saved minimum-wage assessment
            # no longer reconciles to the original top-up.

            original_assessment = dict(
                original.minimum_wage_assessment
            )

            modified_assessment = dict(original_assessment)
            modified_assessment["top_up"] = "1100.00"

            original.minimum_wage_assessment = modified_assessment

            with pytest.raises(
                PayrollMinimumWageRederivationError,
                match="Saved minimum wage assessment",
            ):
                await verify()

            original.minimum_wage_assessment = original_assessment

            assert await verify() == first

            print("ALTERED SAVED ASSESSMENT = REJECTED")
            print("SAVED ASSESSMENT RESTORATION = PASS")

            # Mutation 2: a stored top-up amount changes,
            # breaking reconciliation to the original gross.

            original_top_up = top_up_line.amount

            # Re-derive against isolated in-memory copies.
            # Never mutate a historical ORM calculation line.
            # The re-derivation service accepts dictionaries
            # and mapped ORM rows, but not SimpleNamespace.
            # Build detached dictionaries without modifying ORM.

            _original_stored_lines = stored_lines

            _tampered_lines = [
                {
                    column.key: getattr(item, column.key)
                    for column in item.__mapper__.columns
                }
                for item in stored_lines
            ]

            _tampered_top_up = next(
                item
                for item in _tampered_lines
                if item["line_type"] == "minimum_wage"
            )

            _tampered_top_up["amount"] = Decimal("1100.00")

            assert top_up_line.amount == original_top_up

            stored_lines = _tampered_lines


            with pytest.raises(
                PayrollMinimumWageRederivationError,
                match="gross reconciliation",
            ):
                await verify()

            # Restore the original in-memory source list.
            stored_lines = _original_stored_lines

            assert top_up_line.amount == original_top_up

            # Separately prove PostgreSQL prevents mutation
            # of the real historical calculation line.
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
                            "new_amount": Decimal("1100.00"),
                            "company_id": company.id,
                            "line_id": top_up_line.id,
                        },
                    )

            await db.refresh(top_up_line)
            assert top_up_line.amount == original_top_up

            print("HISTORICAL TOP-UP UPDATE = BLOCKED")
            print("ORIGINAL TOP-UP AMOUNT = UNCHANGED")


            assert await verify() == first

            print("ALTERED TOP-UP LINE = REJECTED")
            print("TOP-UP RESTORATION = PASS")

            # Mutation 3: original salary earnings change.
            # The saved gross must still reconcile exactly.

            original_salary_amount = salary_line.amount

            # Mutation 3: verify a changed salary amount
            # using detached dictionaries, not persisted ORM rows.
            _salary_original_lines = stored_lines

            _salary_tampered_lines = [
                {
                    column.key: getattr(item, column.key)
                    for column in item.__mapper__.columns
                }
                for item in stored_lines
            ]

            _salary_tampered_row = next(
                item
                for item in _salary_tampered_lines
                if item["line_type"] == "salary"
            )

            _salary_tampered_row["amount"] = Decimal("3900.00")

            assert salary_line.amount == original_salary_amount

            stored_lines = _salary_tampered_lines

            with pytest.raises(
                PayrollMinimumWageRederivationError,
                match="gross reconciliation",
            ):
                await verify()

            # Restore the original in-memory verification inputs.
            stored_lines = _salary_original_lines

            assert salary_line.amount == original_salary_amount

            # PostgreSQL must reject the same mutation
            # against the actual historical salary line.
            from sqlalchemy.exc import IntegrityError as _SalaryIntegrityError

            with pytest.raises(
                _SalaryIntegrityError,
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
                            "new_amount": Decimal("3900.00"),
                            "company_id": company.id,
                            "line_id": salary_line.id,
                        },
                    )

            await db.refresh(salary_line)

            assert salary_line.amount == original_salary_amount
            assert await verify() == first

            print("HISTORICAL SALARY LINE UPDATE = BLOCKED")
            print("ORIGINAL SALARY AMOUNT = UNCHANGED")

            assert await verify() == first

            print("ALTERED SALARY LINE = REJECTED")
            print("SALARY RESTORATION = PASS")

            calculation_count = await db.scalar(
                select(func.count(PayrollCalculation.id))
                .where(
                    PayrollCalculation.company_id == company.id,
                    PayrollCalculation.payroll_input_id
                    == payroll_input.id,
                )
            )

            correction_count = await db.scalar(
                select(func.count(PayrollCorrection.id))
                .where(
                    PayrollCorrection.company_id == company.id,
                )
            )

            assert calculation_count == 1
            assert correction_count == 0

            await db.refresh(original)

            assert original.revision == 1
            assert original.gross_amount == Decimal("5200.00")
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
            print("HISTORICAL IMMUTABILITY = NOT YET PROVEN")
            print("AUTOMATIC REVISION CREATION = BLOCKED")
            print("FINANCIAL EXECUTION = BLOCKED")
            print("POSTGRESQL RE-DERIVATION E2E = PASS")

    finally:
        await engine.dispose()