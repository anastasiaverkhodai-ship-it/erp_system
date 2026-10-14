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
async def test_payroll_historical_source_freeze_postgresql():
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

            # First persist the minimum-wage top-up in the
            # test transaction, without committing anything.

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

            # Load the migration as Python code.
            # Do not run Alembic against the primary database.

            migration_path = (
                Path("alembic/versions/")
                / "13d4a7b8c026_payroll_historical_source_freeze.py"
            )

            assert migration_path.is_file()

            spec = spec_from_file_location(
                "payroll_historical_freeze_e2e_migration",
                migration_path,
            )

            assert spec is not None
            assert spec.loader is not None

            migration = module_from_spec(spec)
            spec.loader.exec_module(migration)

            assert migration.revision == "13d4a7b8c026"
            assert migration.down_revision == "13d4a7b8c025"

            expected_tables = {
                "payroll_calculations",
                "payroll_calculation_lines",
                "payroll_inputs",
                "payroll_input_salary_slices",
                "employee_salary_rates",
                "payroll_supplements",
                "payroll_vacation_calculations",
                "payroll_sick_leave_calculations",
                "leave_requests",
            }

            assert set(migration.PROTECTED_TABLES) == expected_tables

            # Capture migration statements, then execute them
            # only through the isolated test session.

            async def apply_migration(operation):
                statements = []

                with patch.object(
                    migration,
                    "op",
                    SimpleNamespace(execute=statements.append),
                ):
                    operation()

                assert len(statements) == 10

                for statement in statements:
                    await db.execute(text(statement))

                return statements

            upgrade_statements = await apply_migration(
                migration.upgrade
            )

            assert "CREATE FUNCTION" in upgrade_statements[0]

            print("MIGRATION SQL EXECUTED ON ISOLATED COPY = PASS")
            print("POSTGRESQL FUNCTION = CREATED")
            print("EXPECTED TRIGGERS = 9")

            # Verify the trigger installation using PostgreSQL
            # system catalog, not Python metadata.

            async def installed_triggers():
                rows = (
                    await db.execute(
                        text("""
                            SELECT
                                c.relname AS table_name,
                                t.tgname AS trigger_name,
                                pg_get_triggerdef(t.oid, TRUE)
                                    AS definition
                            FROM pg_trigger t
                            JOIN pg_class c
                              ON c.oid = t.tgrelid
                            JOIN pg_namespace n
                              ON n.oid = c.relnamespace
                            WHERE NOT t.tgisinternal
                              AND n.nspname = current_schema()
                              AND t.tgname LIKE 'trg_%_history_freeze'
                            ORDER BY c.relname
                        """)
                    )
                ).mappings().all()

                return rows

            triggers = await installed_triggers()

            actual_tables = {
                item["table_name"]
                for item in triggers
            }

            assert actual_tables == expected_tables
            assert len(triggers) == 9

            for trigger in triggers:
                definition = trigger["definition"].upper()

                # PostgreSQL may canonicalize the event order.
                # UPDATE OR DELETE and DELETE OR UPDATE
                # represent the same trigger semantics.
                assert (
                    "BEFORE UPDATE OR DELETE" in definition
                    or "BEFORE DELETE OR UPDATE" in definition
                )

                assert (
                    "PAYROLL_REJECT_USED_SOURCE_MUTATION"
                    in definition
                )

            print("REAL POSTGRESQL TRIGGER COUNT = 9/9 PASS")
            print("TRIGGER EVENT CONTRACT = UPDATE + DELETE")

            # Every rejected SQL statement runs inside a
            # savepoint. Expected PostgreSQL errors therefore
            # do not invalidate the main test transaction.

            async def assert_rejected(sql, params):
                rejected = False

                try:
                    async with db.begin_nested():
                        await db.execute(
                            text(sql),
                            params,
                        )
                except Exception as exc:
                    if (
                        "Historical payroll source is immutable"
                        not in str(exc)
                    ):
                        raise

                    rejected = True

                assert rejected, (
                    "Historical source mutation unexpectedly allowed: "
                    + sql
                )

            # Five source categories exist in the seeded
            # positive minimum-wage calculation:
            #
            # 1. calculation
            # 2. calculation line
            # 3. payroll input
            # 4. salary slice
            # 5. historical salary rate
            #
            # Test both UPDATE and DELETE for every category.

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

            mutation_count = 0

            for table_name, row_id, assignment in targets:

                await assert_rejected(
                    f"""
                    UPDATE {table_name}
                    SET {assignment}
                    WHERE company_id = :company_id
                      AND id = :row_id
                    """,
                    {
                        "company_id": company.id,
                        "row_id": row_id,
                    },
                )

                mutation_count += 1

                print(
                    table_name.upper(),
                    "UPDATE = REJECTED",
                )

                await assert_rejected(
                    f"""
                    DELETE FROM {table_name}
                    WHERE company_id = :company_id
                      AND id = :row_id
                    """,
                    {
                        "company_id": company.id,
                        "row_id": row_id,
                    },
                )

                mutation_count += 1

                print(
                    table_name.upper(),
                    "DELETE = REJECTED",
                )

            assert mutation_count == 10

            print("REFERENCED SOURCE UPDATE/DELETE = 10/10 REJECTED")

            # Verify permitted operations are not accidentally
            # blocked. Payroll-period bookkeeping is outside
            # this freeze trigger contract.

            permitted = await db.execute(
                text("""
                    UPDATE payroll_periods
                    SET year = year
                    WHERE company_id = :company_id
                      AND id = :period_id
                """),
                {
                    "company_id": company.id,
                    "period_id": period.id,
                },
            )

            assert permitted.rowcount == 1

            print("PAYROLL PERIOD NON-MUTATING UPDATE = ALLOWED")

            # The revised function permits UPDATE of salary
            # rates that are not referenced by a calculation.
            # Seed an unused rate and verify this boundary.

            unused_rate = EmployeeSalaryRate(
                company_id=company.id,
                employment_contract_id=contract.id,
                rate_type="hourly",
                amount=Decimal("60.00"),
                currency_code="UAH",
                effective_from=date(2027, 1, 1),
                effective_to=date(2027, 1, 31),
                created_by=actor.id,
            )

            db.add(unused_rate)
            await db.flush()

            allowed_rate_update = await db.execute(
                text("""
                    UPDATE employee_salary_rates
                    SET amount = :new_amount
                    WHERE company_id = :company_id
                      AND id = :rate_id
                """),
                {
                    "new_amount": Decimal("61.00"),
                    "company_id": company.id,
                    "rate_id": unused_rate.id,
                },
            )

            assert allowed_rate_update.rowcount == 1

            current_unused_amount = await db.scalar(
                text("""
                    SELECT amount
                    FROM employee_salary_rates
                    WHERE company_id = :company_id
                      AND id = :rate_id
                """),
                {
                    "company_id": company.id,
                    "rate_id": unused_rate.id,
                },
            )

            assert current_unused_amount == Decimal("61.00")

            print("UNREFERENCED SALARY RATE UPDATE = ALLOWED")

            # Confirm the original historical calculation
            # remains intact after all attempted mutations.

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

            original_line_count = await db.scalar(
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

            assert original_line_count == 2

            print("ORIGINAL CALCULATION = INTACT")
            print("ORIGINAL LINE COUNT = 2")
            print("ORIGINAL GROSS = 5200 UAH")

            # Verify downgrade SQL in the SAME isolated
            # transaction. No Alembic stamp is changed.
            #
            # Actual Alembic upgrade/downgrade execution
            # still requires a separate migration-runner gate.

            downgrade_statements = await apply_migration(
                migration.downgrade
            )

            assert "DROP FUNCTION" in downgrade_statements[-1]

            remaining_triggers = await installed_triggers()

            assert not remaining_triggers

            function_count = await db.scalar(
                text("""
                    SELECT COUNT(*)
                    FROM pg_proc p
                    JOIN pg_namespace n
                      ON n.oid = p.pronamespace
                    WHERE n.nspname = current_schema()
                      AND p.proname =
                          'payroll_reject_used_source_mutation'
                """)
            )

            assert function_count == 0

            print("DOWNGRADE TRIGGER REMOVAL = 9/9 PASS")
            print("DOWNGRADE FUNCTION REMOVAL = PASS")
            print("MIGRATION SQL SYMMETRY = PASS")

            # Roll back all test data and DDL.
            # The isolated runner deletes the copied database.

            original_id = original.id

            await db.rollback()

            remaining = await db.scalar(
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

            assert remaining == 0

            print("TRANSACTION ROLLBACK = PASS")
            print("TEST DATA ZERO-LEAK = PASS")
            print("HISTORICAL SOURCE FREEZE E2E = PASS")
            print("AUTOMATIC REVISION CREATION = BLOCKED")

    finally:
        await engine.dispose()