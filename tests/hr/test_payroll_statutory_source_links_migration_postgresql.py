"""Real PostgreSQL test for c044 source-link provenance schema."""

import os
from datetime import date

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine

from app.models.payroll_statutory import PayrollStatutoryLineSource


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_E2E") != "1",
        reason="Real PostgreSQL required",
    ),
]


async def test_real_postgresql_statutory_source_link_schema(
    employee_engine,
):
    def inspect_database(sync_connection):
        inspector = inspect(sync_connection)

        assert inspector.has_table(
            "payroll_statutory_line_sources"
        )

        constraints = inspector.get_foreign_keys(
            "payroll_statutory_line_sources"
        )

        fk_names = {item["name"] for item in constraints}

        assert "fk_psls_company_statutory_line" in fk_names
        assert "fk_psls_company_income_line" in fk_names

        for fk in constraints:
            if fk["name"] == "fk_psls_company_statutory_line":
                assert fk["constrained_columns"] == [
                    "company_id", "statutory_line_id"
                ]
            if fk["name"] == "fk_psls_company_income_line":
                assert fk["constrained_columns"] == [
                    "company_id", "payroll_calculation_line_id"
                ]

        uniques = {
            item["name"]
            for item in inspector.get_unique_constraints(
                "payroll_statutory_line_sources"
            )
        }

        assert "uq_psls_tax_income_link" in uniques

        checks = {
            item["name"]
            for item in inspector.get_check_constraints(
                "payroll_statutory_line_sources"
            )
        }

        assert "ck_psls_source_amount" in checks
        assert "ck_psls_income_dates" in checks

        parent_uniques = {
            item["name"]
            for item in inspector.get_unique_constraints(
                "payroll_statutory_result_lines"
            )
        }

        assert "uq_psrl_company_id" in parent_uniques

    async with employee_engine.connect() as conn:
        await conn.run_sync(inspect_database)

    print("F2-C3-B2-B PG TABLE = PASS")
    print("F2-C3-B2-B COMPOSITE FKS = PASS")
    print("F2-C3-B2-B UNIQUE/CHECKS = PASS")

    # Check that the SQLAlchemy mapping matches actual columns.
    async with employee_engine.connect() as conn:
        def validate_columns(sync_connection):
            database_columns = {
                item["name"]
                for item in inspect(sync_connection).get_columns(
                    "payroll_statutory_line_sources"
                )
            }
            orm_columns = {
                column.name
                for column in PayrollStatutoryLineSource.__table__.columns
            }
            assert database_columns == orm_columns, (
                database_columns,
                orm_columns,
            )

        await conn.run_sync(validate_columns)

    print("F2-C3-B2-B ORM/DB COLUMNS = PASS")

    # Invalid FK and invalid numeric/date payloads must fail
    # in real PostgreSQL. Each case has its own rolled-back savepoint.
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as session:

        before = await session.scalar(
            text(
                "SELECT count(*) FROM "
                "payroll_statutory_line_sources"
            )
        )

        cases = (
            (
                "invalid_fk",
                1,
                2147483647,
                2147483647,
                "10.00",
                date(2026, 9, 1),
                date(2026, 9, 14),
            ),
            (
                "negative_amount",
                1,
                2147483647,
                2147483647,
                "-1.00",
                date(2026, 9, 1),
                date(2026, 9, 14),
            ),
            (
                "reversed_dates",
                1,
                2147483647,
                2147483647,
                "10.00",
                date(2026, 9, 15),
                date(2026, 9, 14),
            ),
        )

        for (
            case,
            company_id,
            statutory_line_id,
            source_line_id,
            amount,
            first_day,
            last_day,
        ) in cases:

            savepoint = await session.begin_nested()

            try:
                with pytest.raises(IntegrityError):
                    await session.execute(
                        text(
                            "INSERT INTO payroll_statutory_line_sources "
                            "(company_id, statutory_line_id, "
                            "payroll_calculation_line_id, source_amount, "
                            "income_effective_from, income_effective_to) "
                            "VALUES (:company_id, :statutory_line_id, "
                            ":source_line_id, :amount, "
                            ":first_day, :last_day)"
                        ),
                        {
                            "company_id": company_id,
                            "statutory_line_id": statutory_line_id,
                            "source_line_id": source_line_id,
                            "amount": amount,
                            "first_day": first_day,
                            "last_day": last_day,
                        },
                    )

                print(
                    "F2-C3-B2-B DB REJECTION = PASS",
                    case,
                )

            finally:
                if savepoint.is_active:
                    await savepoint.rollback()

        after = await session.scalar(
            text(
                "SELECT count(*) FROM "
                "payroll_statutory_line_sources"
            )
        )

        assert after == before
        await session.rollback()

    print("F2-C3-B2-B ZERO LEAK = PASS")
    print("F2-C3-B2-B REAL PG MIGRATION E2E = PASS")
