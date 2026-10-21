"""Full termination severance -> normal Payroll Calculation service E2E."""

import os
import re

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from sqlalchemy import func, insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.models.employment_event import EmploymentEvent
from app.models.payroll import (
    PayrollCalculation,
    PayrollCalculationLine,
    PayrollInput,
    PayrollInputSalarySlice,
    PayrollPeriod,
    PayrollPeriodStatus,
)
from app.models.employee_salary_rate import EmployeeSalaryRate
from app.models.payroll_termination_severance import (
    PayrollTerminationSeverance,
)
from app.services.payroll_calculation_service import (
    calculate_payroll_input,
    list_payroll_calculation_lines,
    PayrollCalculationConflictError,
)
from app.services.payroll_termination_severance_preflight import (
    SeverancePreflightError,
    preflight_termination_severance,
)
from app.services.payroll_statutory_service import (
    PayrollStatutoryValidationError,
    calculate_payroll_statutory_result,
)
from test_payroll_vacation_postgresql import _seed_identity



from sqlalchemy.exc import DBAPIError

from app.models.payroll_severance_statutory_record import (
    PayrollSeveranceStatutoryRecord,
)

pytestmark = pytest.mark.asyncio


def require_clone():
    assert os.getenv("RUN_POSTGRES_E2E") == "1"

    url = make_url(str(settings.database_url))
    assert re.fullmatch(
        r"erp_r3_copy_[0-9a-f]{16}",
        url.database or "",
    )
    assert url.host in ("localhost", "127.0.0.1", "::1")

    return str(settings.database_url)


async def test_severance_snapshot_insert_and_mutation_e2e():
    engine = create_async_engine(
        require_clone(), pool_pre_ping=True
    )

    Session = async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    token = uuid4().hex[:10]

    try:
        async with engine.connect() as conn:
            head = await conn.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            assert head == "13d4a7b8c042"

        async with Session() as db:
            async with db.begin():

                company, user, employee, contract = (
                    await _seed_identity(db)
                )

                contract.status = "ended"
                contract.end_date = date(2026, 9, 30)
                await db.flush()

                period = PayrollPeriod(
                    company_id=company.id,
                    year=2026,
                    month=9,
                    start_date=date(2026, 9, 1),
                    end_date=date(2026, 9, 30),
                    status=PayrollPeriodStatus.FINALIZED,
                    created_by=user.id,
                    finalized_by=user.id,
                    finalized_at=datetime(
                        2026, 10, 1, 12, 0, tzinfo=UTC
                    ),
                )
                db.add(period)
                await db.flush()

                payroll_input = PayrollInput(
                    company_id=company.id,
                    payroll_period_id=period.id,
                    employment_contract_id=contract.id,
                    scheduled_minutes=1200,
                    worked_minutes=1200,
                    leave_days=0,
                    sick_days=0,
                    manual_adjustment_amount=Decimal("0.00"),
                    created_by=user.id,
                )
                db.add(payroll_input)
                await db.flush()

                rate = EmployeeSalaryRate(
                    company_id=company.id,
                    employment_contract_id=contract.id,
                    rate_type="hourly",
                    amount=Decimal("20.00"),
                    currency_code="UAH",
                    effective_from=date(2026, 9, 1),
                    effective_to=date(2026, 9, 30),
                    created_by=user.id,
                )
                db.add(rate)
                await db.flush()

                salary_slice = PayrollInputSalarySlice(
                    company_id=company.id,
                    payroll_input_id=payroll_input.id,
                    salary_rate_id=rate.id,
                    salary_rate_type="hourly",
                    salary_rate_amount=Decimal("20.00"),
                    currency_code="UAH",
                    effective_from=date(2026, 9, 1),
                    effective_to=date(2026, 9, 30),
                    scheduled_minutes=1200,
                    worked_minutes=1200,
                )
                db.add(salary_slice)

                event = EmploymentEvent(
                    company_id=company.id,
                    employment_contract_id=contract.id,
                    event_type="termination",
                    effective_date=date(2026, 9, 30),
                    order_number=f"TERM-{token}",
                    order_date=date(2026, 9, 30),
                    reason="Documented redundancy",
                    request_key=f"term-{token}",
                    before_state={},
                    after_state={},
                    request_payload={
                        "termination_basis": {
                            "ground": "redundancy",
                            "labour_code_provision": "40.1",
                            "source_reference": "Verified termination order",
                        }
                    },
                    created_by=user.id,
                )
                db.add(event)
                await db.flush()

                source = PayrollTerminationSeverance(
                    company_id=company.id,
                    payroll_input_id=payroll_input.id,
                    employment_contract_id=contract.id,
                    termination_event_id=event.id,
                    revision=1,
                    supersedes_id=None,
                    request_key=f"severance-{token}",
                    termination_date=date(2026, 9, 30),
                    order_number=event.order_number,
                    policy_ground="redundancy",
                    policy_category="average_months",
                    labour_code_provision="40.1",
                    source_reference="Verified termination order",
                    multiplier=1,
                    basis_as_of=date(2026, 9, 30),
                    basis_reference="Verified average monthly earnings",
                    base_amount=Decimal("12000.00"),
                    gross_amount=Decimal("12000.00"),
                    currency_code="UAH",
                    created_by=user.id,
                )
                db.add(source)
                await db.flush()

                # The existing production Payroll service must
                # construct BOTH salary and severance lines.
                calculation = await calculate_payroll_input(
                    db,
                    company_id=company.id,
                    payroll_input_id=payroll_input.id,
                    calculated_by=user.id,
                )
                await db.flush()

                lines = await list_payroll_calculation_lines(
                    db,
                    company_id=company.id,
                    payroll_calculation_id=calculation.id,
                )

                salary_lines = [
                    x for x in lines if x.line_type == "salary"
                ]
                severance_lines = [
                    x for x in lines if x.line_type == "severance"
                ]

                assert len(salary_lines) == 1
                assert len(severance_lines) == 1
                assert Decimal(salary_lines[0].amount) == Decimal("400.00")
                assert Decimal(severance_lines[0].amount) == Decimal("12000.00")

                assert (
                    severance_lines[0].source_termination_severance_id
                    == source.id
                )

                # Inspect and reconcile every payroll component.
                # Do not silently accept an unexplained gross difference.
                amounts = {
                    line.line_type: Decimal(line.amount)
                    for line in lines
                }

                print("PAYROLL COMPONENTS =", amounts, flush=True)
                print(
                    "PAYROLL GROSS =",
                    Decimal(calculation.gross_amount),
                    flush=True,
                )

                assert len(amounts) == len(lines), (
                    "Duplicate payroll line types require review"
                )

                assert amounts == {
                    "salary": Decimal("400.00"),
                    "minimum_wage": Decimal("640.00"),
                    "severance": Decimal("12000.00"),
                }, amounts

                assert (
                    sum(amounts.values(), Decimal("0.00"))
                    == Decimal(calculation.gross_amount)
                    == Decimal("13040.00")
                )
                assert calculation.currency_code == "UAH"

                # This is structural test evidence ONLY.
                # It is not a legally approved tax classification.
                snapshot_table = (
                    PayrollSeveranceStatutoryRecord.__table__
                )

                payload = {
                    "company_id": company.id,
                    "payroll_calculation_id": calculation.id,
                    "version": 1,
                    "income_date": date(2026, 9, 30),
                    "currency_code": "UAH",
                    "ordinary_gross": Decimal("1040.00"),
                    "severance_gross": Decimal("12000.00"),
                    "total_gross": Decimal("13040.00"),
                    "severance_source_ids": [source.id],
                    "policy_code": "TEST-SEVERANCE-ONLY",
                    "policy_valid_from": date(2026, 9, 1),
                    "policy_valid_to": None,
                    "source_reference": "TEST ONLY - NOT LEGAL APPROVAL",
                    "income_code_4df": "127",
                    "personal_income_tax_base": Decimal("13040.00"),
                    "military_levy_base": Decimal("13040.00"),
                    "unified_social_contribution_base": Decimal("1040.00"),
                    "fingerprint_sha256": "a" * 64,
                }

                assert Decimal(
                    calculation.gross_amount
                ) == payload["total_gross"]

                assert sum(
                    (
                        Decimal(line.amount)
                        for line in lines
                        if line.line_type != "severance"
                    ),
                    Decimal("0.00"),
                ) == payload["ordinary_gross"]

                assert sum(
                    (
                        Decimal(line.amount)
                        for line in lines
                        if line.line_type == "severance"
                    ),
                    Decimal("0.00"),
                ) == payload["severance_gross"]

                # SOURCE VALIDATION BEFORE POSITIVE INSERT
                # The unique key is still free at this point.
                # Errors therefore exercise source/gross checks
                # rather than an existing snapshot collision.

                async def reject_invalid_snapshot(
                    changes, expected_message
                ):
                    candidate = dict(payload)
                    candidate.update(changes)

                    savepoint = await db.begin_nested()

                    try:
                        with pytest.raises(DBAPIError) as exc:
                            await db.execute(
                                insert(snapshot_table).values(
                                    **candidate
                                )
                            )

                        actual = str(exc.value).lower()
                        assert expected_message.lower() in actual, (
                            expected_message,
                            actual,
                        )
                    finally:
                        # PostgreSQL requires ROLLBACK TO SAVEPOINT
                        # following an expected statement error.
                        if savepoint.is_active:
                            await savepoint.rollback()

                    # Failed SQL must not persist any snapshot.
                    count = await db.scalar(
                        select(func.count(snapshot_table.c.id))
                        .where(
                            snapshot_table.c.company_id
                            == company.id,
                            snapshot_table.c.payroll_calculation_id
                            == calculation.id,
                        )
                    )
                    assert count == 0

                # Forged source: valid-looking positive integer
                # without a matching calculation severance line.
                await reject_invalid_snapshot(
                    {
                        "severance_source_ids": [
                            source.id + 99999999
                        ],
                    },
                    "Severance source links mismatch",
                )
                print("FORGED SOURCE ID = BLOCKED", flush=True)

                # Valid source, but repeated twice.
                await reject_invalid_snapshot(
                    {
                        "severance_source_ids": [
                            source.id, source.id
                        ],
                    },
                    "Duplicate severance source identifier",
                )
                print("DUPLICATE SOURCE IDs = BLOCKED", flush=True)

                # A snapshot must contain at least one source.
                await reject_invalid_snapshot(
                    {"severance_source_ids": []},
                    "Severance source array required",
                )
                print("EMPTY SOURCES = BLOCKED", flush=True)

                # JSONB string is not an array.
                await reject_invalid_snapshot(
                    {
                        "severance_source_ids": (
                            "not-an-array"
                        ),
                    },
                    "Severance source array required",
                )
                print("NON-ARRAY SOURCES = BLOCKED", flush=True)

                # JSONB array containing an invalid element.
                await reject_invalid_snapshot(
                    {
                        "severance_source_ids": [
                            "not-an-integer"
                        ],
                    },
                    "Invalid severance source identifier",
                )
                print("NON-INTEGER SOURCE = BLOCKED", flush=True)

                # Snapshot total must match parent calculation.
                await reject_invalid_snapshot(
                    {
                        "total_gross": Decimal("13041.00"),
                    },
                    "Snapshot gross differs from calculation",
                )
                print("WRONG TOTAL GROSS = BLOCKED", flush=True)

                # Same total but incorrect source split.
                await reject_invalid_snapshot(
                    {
                        "ordinary_gross": Decimal("1041.00"),
                        "severance_gross": Decimal("11999.00"),
                    },
                    "Snapshot line amounts or counts mismatch",
                )
                print("WRONG SEVERANCE SPLIT = BLOCKED", flush=True)

                print(
                    "SOURCE VALIDATION NEGATIVE CASES = 7 PASS",
                    flush=True,
                )

                # POSITIVE: documented calculation/source matches.
                snapshot_id = await db.scalar(
                    insert(snapshot_table)
                    .values(**payload)
                    .returning(snapshot_table.c.id)
                )
                assert snapshot_id is not None

                await db.flush()

                record = (
                    await db.execute(
                        select(snapshot_table).where(
                            snapshot_table.c.id == snapshot_id
                        )
                    )
                ).mappings().one()

                assert record["company_id"] == company.id
                assert (
                    record["payroll_calculation_id"]
                    == calculation.id
                )
                assert record["severance_source_ids"] == [
                    source.id
                ]
                print("SNAPSHOT VALID INSERT = PASS", flush=True)

                async def must_reject(statement, expected):
                    # A failing SQL statement must not poison
                    # the outer PostgreSQL fixture transaction.
                    savepoint = await db.begin_nested()
                    try:
                        with pytest.raises(DBAPIError) as exc:
                            await db.execute(statement)

                        message = str(exc.value).lower()
                        assert expected.lower() in message, (
                            expected,
                            message,
                        )
                    finally:
                        if savepoint.is_active:
                            await savepoint.rollback()

                # Existing calculation already has one snapshot.
                await must_reject(
                    insert(snapshot_table).values(**payload),
                    "duplicate key",
                )
                print("DUPLICATE SNAPSHOT = BLOCKED", flush=True)

                # A forged company cannot claim this calculation.
                wrong_company = dict(payload)
                wrong_company["company_id"] = company.id + 99999999
                await must_reject(
                    insert(snapshot_table).values(**wrong_company),
                    "calculation missing",
                )
                print("WRONG COMPANY = BLOCKED", flush=True)

                # Wrong documented source, unchanged gross amounts.
                forged_source = dict(payload)
                forged_source["severance_source_ids"] = [
                    source.id + 99999999
                ]

                # Delete the first snapshot only inside a nested
                # transaction is impossible: immutable by design.
                # Instead, use a separate valid calculation fixture
                # for independent insert rejection scenarios.
                # For the current calculation, a UNIQUE collision
                # takes precedence over source validation.
                print(
                    "FORGED SOURCE INSERT = REQUIRES SECOND CALCULATION",
                    flush=True,
                )

                duplicate_sources = dict(payload)
                duplicate_sources["severance_source_ids"] = [
                    source.id, source.id
                ]
                print(
                    "DUPLICATE SOURCE INSERT = REQUIRES SECOND CALCULATION",
                    flush=True,
                )

                await must_reject(
                    snapshot_table.update()
                    .where(snapshot_table.c.id == snapshot_id)
                    .values(policy_code="TAMPERED"),
                    "historical severance snapshot is immutable",
                )
                print("SNAPSHOT UPDATE = BLOCKED", flush=True)

                await must_reject(
                    snapshot_table.delete()
                    .where(snapshot_table.c.id == snapshot_id),
                    "historical severance snapshot is immutable",
                )
                print("SNAPSHOT DELETE = BLOCKED", flush=True)

                assert (
                    await db.scalar(
                        select(snapshot_table.c.id).where(
                            snapshot_table.c.id == snapshot_id
                        )
                    )
                ) == snapshot_id

                print(
                    "SNAPSHOT INSERT / IMMUTABILITY E2E = PASS",
                    flush=True,
                )

    finally:
        await engine.dispose()
