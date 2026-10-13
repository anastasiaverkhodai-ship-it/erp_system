"""Phase 13.7-C3-B2: concurrent attendance/finalization state races.

MANDATORY:
    Run through scripts/run_postgresql_regression_copy.py.

Test business writes are allowed ONLY in erp_r3_copy_*.
Never run these tests against the main ERP database.
"""

import asyncio
import os

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll import (
    PayrollInput,
    PayrollPeriod,
    PayrollPeriodStatus,
)
from app.models.time_attendance import AttendanceRecord

from app.schemas.payroll import (
    PayrollPeriodCreate,
    PayrollInputCreate,
)
from app.schemas.time_attendance import (
    AttendanceCreate,
    AttendanceUpdate,
)

from app.services.payroll_period_service import (
    create_payroll_period,
    finalize_payroll_period,
)
from app.services.payroll_input_service import (
    create_payroll_input,
)
from app.services.payroll_calculation_service import (
    calculate_payroll_input,
)
from app.services.time_attendance_service import (
    create_attendance,
    update_attendance,
    TimeAttendanceLifecycleError,
)

from test_employee_foundation import employee_engine
from test_payroll_source_locking import sources


def _check_isolation():
    """Reject any database except the disposable PG clone."""

    assert os.environ.get("RUN_POSTGRES_E2E") == "1"

    url = os.environ.get("DATABASE_URL")
    assert url, "STOP: DATABASE_URL missing"

    parsed = make_url(url)

    assert parsed.get_backend_name() == "postgresql"

    database = parsed.database or ""

    assert database.startswith("erp_r3_copy_"), (
        "STOP: refusing to run against non-isolated database"
    )

    return database


def _value(value):
    return getattr(value, "value", value)


async def _seed_payroll(employee_engine):
    """Create complete payroll data in the isolated PostgreSQL clone.

    The existing sources(db) helper manages its own transactions.
    Therefore do not wrap it in an outer db.begin() context.
    """

    expected_database = _check_isolation()

    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:

        actual_database = await db.scalar(
            text("SELECT current_database()")
        )

        assert actual_database == expected_database
        assert actual_database.startswith("erp_r3_copy_")

        # Reuse the verified existing ERP fixture.
        # It is allowed to manage its own transactions.
        fixture, contract, _ = await sources(db)

        company_id = fixture["company"]
        actor_id = fixture["actor"]

        period = await create_payroll_period(
            db,
            company_id=company_id,
            created_by=actor_id,
            data=PayrollPeriodCreate(
                year=2026,
                month=9,
                start_date=date(2026, 9, 1),
                end_date=date(2026, 9, 30),
            ),
        )

        first_attendance_id = None
        attendance_count = 0

        for day in range(1, 31):

            work_date = date(2026, 9, day)

            if work_date.weekday() > 4:
                continue

            attendance = await create_attendance(
                db,
                company_id=company_id,
                created_by=actor_id,
                data=AttendanceCreate(
                    employment_contract_id=contract.id,
                    work_date=work_date,
                    status="absent",
                ),
            )

            attendance_count += 1

            if first_attendance_id is None:
                first_attendance_id = attendance.id

        payroll_input = await create_payroll_input(
            db,
            company_id=company_id,
            payroll_period_id=period.id,
            created_by=actor_id,
            data=PayrollInputCreate(
                employment_contract_id=contract.id,
            ),
        )

        assert payroll_input.worked_minutes == 0
        assert first_attendance_id is not None
        assert attendance_count > 0

        ids = {
            "company": company_id,
            "actor": actor_id,
            "period": period.id,
            "input": payroll_input.id,
            "attendance": first_attendance_id,
        }

        # Commit only inside the disposable PostgreSQL clone.
        # The isolation check above is mandatory.
        await db.commit()

    print("ISOLATED PAYROLL FIXTURE = PASS")
    print("ATTENDANCE RECORDS =", attendance_count)
    print("INITIAL WORKED MINUTES = 0")
    print("FIXTURE COMMIT = ISOLATED CLONE ONLY")

    return ids


def _present_update():
    return AttendanceUpdate(
        status="present",
        actual_start_at=datetime(
            2026, 9, 1, 9,
            tzinfo=ZoneInfo("Europe/Kyiv"),
        ),
        actual_end_at=datetime(
            2026, 9, 1, 17,
            tzinfo=ZoneInfo("Europe/Kyiv"),
        ),
    )


async def _check_persisted_state(
    employee_engine,
    ids,
    *,
    expected_minutes,
):
    """Independently read final persisted business state."""

    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:

        period = await db.scalar(
            select(PayrollPeriod).where(
                PayrollPeriod.company_id == ids["company"],
                PayrollPeriod.id == ids["period"],
            )
        )

        payroll_input = await db.scalar(
            select(PayrollInput).where(
                PayrollInput.company_id == ids["company"],
                PayrollInput.id == ids["input"],
            )
        )

        attendance = await db.scalar(
            select(AttendanceRecord).where(
                AttendanceRecord.company_id == ids["company"],
                AttendanceRecord.id == ids["attendance"],
            )
        )

        assert period is not None
        assert payroll_input is not None
        assert attendance is not None

        assert (
            _value(period.status)
            == PayrollPeriodStatus.FINALIZED.value
        )

        assert payroll_input.worked_minutes == expected_minutes

        assert attendance.worked_minutes == expected_minutes

        print("PERSISTED PERIOD STATUS = FINALIZED")
        print(
            "PERSISTED PAYROLL MINUTES =",
            payroll_input.worked_minutes,
        )
        print(
            "PERSISTED ATTENDANCE MINUTES =",
            attendance.worked_minutes,
        )


@pytest.mark.asyncio
async def test_finalization_first_rejects_late_attendance_update(
    employee_engine,
):
    """Finalization wins; late attendance edit must be rejected."""

    ids = await _seed_payroll(employee_engine)

    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as finalizer:

        async with AsyncSession(
            employee_engine,
            expire_on_commit=False,
        ) as editor:

            # Transaction A finalizes, but does not commit yet.
            async with finalizer.begin():

                await finalize_payroll_period(
                    finalizer,
                    company_id=ids["company"],
                    payroll_period_id=ids["period"],
                    finalized_by=ids["actor"],
                )

                print(
                    "FINALIZATION TRANSACTION = "
                    "UNCOMMITTED"
                )

                # Transaction B attempts a late update.
                async def late_edit():

                    async with editor.begin():

                        await update_attendance(
                            editor,
                            company_id=ids["company"],
                            attendance_id=ids["attendance"],
                            data=_present_update(),
                            changed_by=ids["actor"],
                        )

                task = asyncio.create_task(late_edit())

                try:
                    # The edit must remain blocked while
                    # finalization owns the company lock.
                    await asyncio.sleep(0.2)

                    assert not task.done(), (
                        "STOP: late edit bypassed company lock"
                    )

                    print(
                        "LATE ATTENDANCE UPDATE "
                        "WAITS FOR FINALIZATION = PASS"
                    )

                except BaseException:
                    task.cancel()
                    await asyncio.gather(
                        task,
                        return_exceptions=True,
                    )
                    raise

            # Transaction A committed its FINALIZED state.
            print("FINALIZATION COMMIT = PASS")

            with pytest.raises(
                TimeAttendanceLifecycleError,
                match="finalized",
            ):
                await asyncio.wait_for(
                    task,
                    timeout=10,
                )

            print("LATE ATTENDANCE UPDATE = REJECTED")

    await _check_persisted_state(
        employee_engine,
        ids,
        expected_minutes=0,
    )

    print("FINALIZATION FIRST RACE = PASS")


@pytest.mark.asyncio
async def test_attendance_first_is_used_by_finalization(
    employee_engine,
):
    """Attendance wins; finalization must use new worked minutes."""

    ids = await _seed_payroll(employee_engine)

    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as editor:

        async with AsyncSession(
            employee_engine,
            expire_on_commit=False,
        ) as finalizer:

            # Transaction A updates attendance without committing.
            async with editor.begin():

                updated = await update_attendance(
                    editor,
                    company_id=ids["company"],
                    attendance_id=ids["attendance"],
                    data=_present_update(),
                    changed_by=ids["actor"],
                )

                assert updated.worked_minutes == 480

                print(
                    "ATTENDANCE UPDATE = "
                    "480 MINUTES UNCOMMITTED"
                )

                # Transaction B attempts finalization while
                # the company lock belongs to Transaction A.
                async def finalize_after_edit():

                    async with finalizer.begin():

                        return await finalize_payroll_period(
                            finalizer,
                            company_id=ids["company"],
                            payroll_period_id=ids["period"],
                            finalized_by=ids["actor"],
                        )

                task = asyncio.create_task(
                    finalize_after_edit()
                )

                try:
                    await asyncio.sleep(0.2)

                    assert not task.done(), (
                        "STOP: finalization bypassed company lock"
                    )

                    print(
                        "FINALIZATION WAITS FOR "
                        "ATTENDANCE COMMIT = PASS"
                    )

                except BaseException:
                    task.cancel()
                    await asyncio.gather(
                        task,
                        return_exceptions=True,
                    )
                    raise

            # Transaction A commits the updated attendance.
            print("ATTENDANCE COMMIT = PASS")

            await asyncio.wait_for(
                task,
                timeout=10,
            )

            print("FINALIZATION COMMIT = PASS")

    await _check_persisted_state(
        employee_engine,
        ids,
        expected_minutes=480,
    )

    # Calculate using the verified final payroll snapshot.
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:

        async with db.begin():

            calculation = await calculate_payroll_input(
                db,
                company_id=ids["company"],
                payroll_input_id=ids["input"],
                calculated_by=ids["actor"],
            )

            assert calculation.gross_amount == Decimal(
                "1000.00"
            ), (
                "STOP: payroll calculation did not use "
                "updated attendance snapshot"
            )

            print(
                "GROSS AMOUNT =",
                calculation.gross_amount,
            )

    print("ATTENDANCE FIRST RACE = PASS")
    print("PAYROLL SNAPSHOT RECONCILIATION = PASS")
