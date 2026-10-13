"""Phase 13.7-C3: real PostgreSQL payroll mutation lock ordering.

Run ONLY against the disposable isolated PostgreSQL clone.
No financial execution, journal posting, or payment.
"""

import os

import pytest

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company

from app.services.payroll_period_service import (
    finalize_payroll_period,
)

from app.services.time_attendance_service import (
    update_attendance,
)

from app.schemas.time_attendance import AttendanceUpdate

from test_employee_foundation import employee_engine
from test_payroll_deduction_postgresql import _seed_identity


def _assert_isolated_database():
    assert os.environ.get("RUN_POSTGRES_E2E") == "1", (
        "STOP: isolated PostgreSQL runner required"
    )

    value = os.environ.get("DATABASE_URL")
    assert value, "STOP: DATABASE_URL missing"

    url = make_url(value)

    assert url.get_backend_name() == "postgresql"
    assert (url.database or "").startswith("erp_r3_copy_")

    return url.database


def _assert_lock_timeout(exc):
    original = exc.orig

    code = (
        getattr(original, "sqlstate", None)
        or getattr(original, "pgcode", None)
    )

    assert code == "55P03", (
        "Expected PostgreSQL lock_not_available (55P03); "
        f"received SQLSTATE {code!r}"
    )


async def _run_contention(employee_engine, operation):
    expected_database = _assert_isolated_database()

    # Create the test company ONLY inside the disposable clone.
    #
    # The fixture must be committed before starting the two
    # competing transactions. Otherwise the second connection
    # cannot see the company row under PostgreSQL isolation.
    #
    # The regression-copy runner is responsible for deleting
    # the entire cloned database after the tests.
    async with employee_engine.begin() as setup_connection:

        actual_database = await setup_connection.scalar(
            text("SELECT current_database()")
        )

        assert actual_database == expected_database
        assert actual_database.startswith("erp_r3_copy_")

        async with AsyncSession(
            bind=setup_connection,
            expire_on_commit=False,
            autoflush=False,
        ) as setup_db:

            company, actor, employee, contract = (
                await _seed_identity(setup_db)
            )

            await setup_db.flush()

            company_id = company.id

            assert type(company_id) is int
            assert company_id > 0

            print(
                "ISOLATED FIXTURE COMPANY CREATED = PASS"
            )

    # The setup transaction is now committed ONLY in the clone.
    #
    # Start two independent connections and transactions.
    async with employee_engine.connect() as first:
        async with employee_engine.connect() as second:

            first_transaction = await first.begin()
            second_transaction = await second.begin()

            try:
                current_first = await first.scalar(
                    text("SELECT current_database()")
                )

                current_second = await second.scalar(
                    text("SELECT current_database()")
                )

                assert current_first == expected_database
                assert current_second == expected_database

                # Use the company created by this test.
                #
                # Do not depend on pre-existing database rows.
                visible_company_id = await first.scalar(
                    select(Company.id)
                    .where(Company.id == company_id)
                )

                assert visible_company_id == company_id, (
                    "STOP: committed fixture not visible"
                )

                print(
                    "ISOLATED FIXTURE VISIBILITY = PASS"
                )

                # Transaction A holds the same company-row lock
                # required by serialized_payroll_mutation().
                locked_company_id = await first.scalar(
                    select(Company.id)
                    .where(Company.id == company_id)
                    .with_for_update()
                )

                assert locked_company_id == company_id

                # A short timeout proves the second transaction
                # cannot pass the shared company-row lock.
                await second.execute(
                    text("SET LOCAL lock_timeout = '350ms'")
                )

                async with AsyncSession(
                    bind=second,
                    expire_on_commit=False,
                    autoflush=False,
                ) as db:

                    with pytest.raises(DBAPIError) as caught:

                        if operation == "finalize":
                            await finalize_payroll_period(
                                db,
                                company_id=company_id,
                                payroll_period_id=2147483647,
                                finalized_by=1,
                            )

                        elif operation == "attendance":
                            await update_attendance(
                                db,
                                company_id=company_id,
                                attendance_id=2147483647,
                                data=AttendanceUpdate(),
                                changed_by=1,
                            )

                        else:
                            raise AssertionError(
                                f"Unknown operation: {operation}"
                            )

                    _assert_lock_timeout(caught.value)

                print(
                    operation.upper(),
                    "COMPETING TRANSACTION = BLOCKED",
                )

            finally:
                # Both transactions are rollback-only.
                if second_transaction.is_active:
                    await second_transaction.rollback()

                if first_transaction.is_active:
                    await first_transaction.rollback()


@pytest.mark.asyncio
async def test_finalization_respects_company_mutation_lock(
    employee_engine,
):
    await _run_contention(
        employee_engine,
        "finalize",
    )

    print("PAYROLL FINALIZATION SERIALIZATION = PASS")


@pytest.mark.asyncio
async def test_attendance_update_respects_company_mutation_lock(
    employee_engine,
):
    await _run_contention(
        employee_engine,
        "attendance",
    )

    print("ATTENDANCE UPDATE SERIALIZATION = PASS")
