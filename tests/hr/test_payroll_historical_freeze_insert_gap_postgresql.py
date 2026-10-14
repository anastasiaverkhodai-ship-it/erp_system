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
async def test_payroll_historical_freeze_insert_gap_postgresql():
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




            from importlib.util import (
                module_from_spec,
                spec_from_file_location,
            )
            from pathlib import Path
            from types import SimpleNamespace
            from unittest.mock import patch

            assert os.environ.get("RUN_POSTGRES_E2E") == "1"
            assert (url.database or "").startswith("erp_r3_copy_")

            # Preserve the complete positive minimum-wage fixture.

            assert minimum_line is not None

            minimum_line["line_no"] = 2

            db.add(
                PayrollCalculationLine(
                    company_id=company.id,
                    payroll_calculation_id=original.id,
                    **minimum_line,
                )
            )

            await db.flush()

            assert original.revision == 1
            assert original.gross_amount == Decimal("5200.00")

            # Install the exact migration SQL in this isolated
            # transaction. Never migrate the primary database.

            migration_path = Path(
                "alembic/versions/"
                "13d4a7b8c026_payroll_historical_source_freeze.py"
            )

            spec = spec_from_file_location(
                "historical_freeze_insert_gap_migration",
                migration_path,
            )

            assert spec is not None
            assert spec.loader is not None

            migration = module_from_spec(spec)
            spec.loader.exec_module(migration)

            assert migration.revision == "13d4a7b8c026"
            assert migration.down_revision == "13d4a7b8c025"

            statements = []

            with patch.object(
                migration,
                "op",
                SimpleNamespace(execute=statements.append),
            ):
                migration.upgrade()

            assert len(statements) == 10

            for statement in statements:
                await db.execute(text(statement))

            print("ISOLATED MIGRATION SQL = PASS")

            # A real database catalog check confirms the
            # installed triggers and their supported events.

            trigger_rows = (
                await db.execute(
                    text("""
                        SELECT
                            c.relname AS table_name,
                            t.tgname AS trigger_name,
                            pg_get_triggerdef(t.oid, true)
                                AS definition
                        FROM pg_trigger t
                        JOIN pg_class c
                          ON c.oid = t.tgrelid
                        JOIN pg_namespace n
                          ON n.oid = c.relnamespace
                        WHERE NOT t.tgisinternal
                          AND n.nspname = current_schema()
                          AND t.tgname LIKE
                              'trg_%_history_freeze'
                        ORDER BY c.relname
                    """)
                )
            ).mappings().all()

            assert len(trigger_rows) == 9

            expected_tables = set(migration.PROTECTED_TABLES)

            assert {
                row["table_name"]
                for row in trigger_rows
            } == expected_tables

            for row in trigger_rows:
                definition = row["definition"].upper()

                assert (
                    "BEFORE UPDATE OR DELETE" in definition
                    or "BEFORE DELETE OR UPDATE" in definition
                )

                assert "BEFORE INSERT" not in definition

            print("EXISTING FREEZE TRIGGERS = 9/9 PASS")
            print("INSERT GUARD = NOT PRESENT")

            # Demonstrate the two known insertion gaps.
            #
            # Every candidate INSERT runs within its own
            # SAVEPOINT and is rolled back immediately.
            # Even accepted test INSERTs cannot persist.

            async def demonstrate_insert(sql, parameters):
                nested = await db.begin_nested()

                try:
                    result = await db.execute(
                        text(sql),
                        parameters,
                    )

                    inserted_id = result.scalar_one()

                    assert isinstance(inserted_id, int)
                    assert inserted_id > 0

                    return inserted_id

                finally:
                    await nested.rollback()

            # GAP 1:
            #
            # A new salary slice can be inserted against an
            # input already referenced by a calculation.
            #
            # Copy every NOT NULL value from the existing
            # slice so the test does not rely on defaults.

            inserted_slice_id = await demonstrate_insert(
                """
                INSERT INTO payroll_input_salary_slices (
                    company_id,
                    payroll_input_id,
                    salary_rate_id,
                    salary_rate_type,
                    salary_rate_amount,
                    currency_code,
                    effective_from,
                    effective_to,
                    scheduled_minutes,
                    worked_minutes,
                    created_at
                )
                SELECT
                    company_id,
                    payroll_input_id,
                    salary_rate_id,
                    salary_rate_type,
                    salary_rate_amount,
                    currency_code,
                    effective_from,
                    effective_to,
                    scheduled_minutes,
                    worked_minutes,
                    created_at
                FROM payroll_input_salary_slices
                WHERE company_id = :company_id
                  AND id = :source_id
                RETURNING id
                """,
                {
                    "company_id": company.id,
                    "source_id": salary_slice.id,
                },
            )

            assert inserted_slice_id != salary_slice.id

            restored_slice_count = await db.scalar(
                text("""
                    SELECT COUNT(*)
                    FROM payroll_input_salary_slices
                    WHERE company_id = :company_id
                      AND payroll_input_id = :payroll_input_id
                """),
                {
                    "company_id": company.id,
                    "payroll_input_id": payroll_input.id,
                },
            )

            assert restored_slice_count == 1

            print("LATE SALARY SLICE INSERT = CURRENTLY ALLOWED")
            print("LATE SALARY SLICE = ROLLED BACK")

            # GAP 2:
            #
            # A new calculation line can be inserted after
            # an original payroll calculation was created.
            #
            # PostgreSQL constraints and UPDATE/DELETE
            # triggers do not currently prohibit this.
            #
            # As with the salary slice, this is a temporary
            # insertion in a rolled-back SAVEPOINT.

            inserted_line_id = await demonstrate_insert(
                """
                INSERT INTO payroll_calculation_lines (
                    company_id,
                    payroll_calculation_id,
                    source_supplement_id,
                    source_sick_leave_calculation_id,
                    source_vacation_calculation_id,
                    line_no,
                    line_type,
                    description,
                    quantity,
                    rate,
                    amount,
                    currency_code,
                    salary_rate_type,
                    source_salary_rate_id,
                    source_effective_from,
                    source_effective_to,
                    created_at
                )
                SELECT
                    company_id,
                    payroll_calculation_id,
                    source_supplement_id,
                    source_sick_leave_calculation_id,
                    source_vacation_calculation_id,
                    3,
                    line_type,
                    description,
                    0,
                    0,
                    0,
                    currency_code,
                    salary_rate_type,
                    source_salary_rate_id,
                    source_effective_from,
                    source_effective_to,
                    created_at
                FROM payroll_calculation_lines
                WHERE company_id = :company_id
                  AND id = :source_id
                RETURNING id
                """,
                {
                    "company_id": company.id,
                    "source_id": line.id,
                },
            )

            assert inserted_line_id != line.id

            restored_line_count = await db.scalar(
                text("""
                    SELECT COUNT(*)
                    FROM payroll_calculation_lines
                    WHERE company_id = :company_id
                      AND payroll_calculation_id = :calculation_id
                """),
                {
                    "company_id": company.id,
                    "calculation_id": original.id,
                },
            )

            assert restored_line_count == 2

            print("LATE CALCULATION LINE INSERT = CURRENTLY ALLOWED")
            print("LATE CALCULATION LINE = ROLLED BACK")

            # Existing UPDATE/DELETE protection must
            # continue rejecting historical mutations.

            async def assert_history_rejected(sql, parameters):
                nested = await db.begin_nested()
                rejected = False

                try:
                    await db.execute(
                        text(sql),
                        parameters,
                    )
                except Exception as exc:
                    if (
                        "Historical payroll source is immutable"
                        not in str(exc)
                    ):
                        raise

                    rejected = True

                finally:
                    await nested.rollback()

                assert rejected, sql

            targets = [
                (
                    "payroll_calculations",
                    original.id,
                    "gross_amount = gross_amount",
                ),
                (
                    "payroll_calculation_lines",
                    line.id,
                    "amount = amount",
                ),
                (
                    "payroll_inputs",
                    payroll_input.id,
                    "worked_minutes = worked_minutes",
                ),
                (
                    "payroll_input_salary_slices",
                    salary_slice.id,
                    "salary_rate_amount = salary_rate_amount",
                ),
                (
                    "employee_salary_rates",
                    rate.id,
                    "amount = amount",
                ),
            ]

            rejected_count = 0

            for table_name, row_id, assignment in targets:

                parameters = {
                    "company_id": company.id,
                    "row_id": row_id,
                }

                await assert_history_rejected(
                    f"""
                    UPDATE {table_name}
                    SET {assignment}
                    WHERE company_id = :company_id
                      AND id = :row_id
                    """,
                    parameters,
                )

                rejected_count += 1

                await assert_history_rejected(
                    f"""
                    DELETE FROM {table_name}
                    WHERE company_id = :company_id
                      AND id = :row_id
                    """,
                    parameters,
                )

                rejected_count += 1

            assert rejected_count == 10

            print("EXISTING UPDATE/DELETE GUARDS = 10/10 PASS")

            # Source evidence and the original payroll gross
            # must remain unchanged after all attacks.

            original_gross = await db.scalar(
                text("""
                    SELECT gross_amount
                    FROM payroll_calculations
                    WHERE company_id = :company_id
                      AND id = :calculation_id
                """),
                {
                    "company_id": company.id,
                    "calculation_id": original.id,
                },
            )

            assert original_gross == Decimal("5200.00")

            assert restored_line_count == 2
            assert restored_slice_count == 1

            print("ORIGINAL REVISION = 1")
            print("ORIGINAL GROSS = 5200 UAH")
            print("ORIGINAL LINES = 2")
            print("ORIGINAL SALARY SLICES = 1")

            # Downgrade only inside the isolated session.

            downgrade_statements = []

            with patch.object(
                migration,
                "op",
                SimpleNamespace(
                    execute=downgrade_statements.append
                ),
            ):
                migration.downgrade()

            assert len(downgrade_statements) == 10

            for statement in downgrade_statements:
                await db.execute(text(statement))

            remaining_triggers = await db.scalar(
                text("""
                    SELECT COUNT(*)
                    FROM pg_trigger t
                    JOIN pg_class c ON c.oid = t.tgrelid
                    JOIN pg_namespace n
                      ON n.oid = c.relnamespace
                    WHERE NOT t.tgisinternal
                      AND n.nspname = current_schema()
                      AND t.tgname LIKE
                          'trg_%_history_freeze'
                """)
            )

            assert remaining_triggers == 0

            print("ISOLATED DOWNGRADE = PASS")
            print("HISTORICAL FREEZE TRIGGERS REMOVED = 9/9")

            original_id = original.id

            await db.rollback()

            leaked = await db.scalar(
                text("""
                    SELECT COUNT(*)
                    FROM payroll_calculations
                    WHERE company_id = :company_id
                      AND id = :calculation_id
                """),
                {
                    "company_id": company.id,
                    "calculation_id": original_id,
                },
            )

            assert leaked == 0

            print("ROLLBACK ZERO-LEAK = PASS")
            print("HISTORICAL INSERT GAP DEMONSTRATION = PASS")
            print("COMPLETE HISTORICAL FREEZE = NOT YET")
            print("AUTOMATIC REVISION CREATION = BLOCKED")

    finally:
        await engine.dispose()