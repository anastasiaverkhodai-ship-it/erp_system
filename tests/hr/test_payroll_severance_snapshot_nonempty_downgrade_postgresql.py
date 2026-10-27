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


async def test_nonempty_snapshot_blocks_downgrade():
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
            assert head == "13d4a7b8c063"

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


                # Calculate a real digest from the canonical
                # severance snapshot payload.
                import hashlib
                import json
                from dataclasses import fields

                from app.services.payroll_severance_statutory_snapshot import (
                    SeveranceStatutorySnapshot,
                    verify_severance_statutory_snapshot,
                    _payload,
                )

                field_names = {
                    field.name
                    for field in fields(SeveranceStatutorySnapshot)
                }

                expected_names = set(payload)

                assert field_names == (
                    expected_names | {"fingerprint_sha256"}
                ), (
                    "Snapshot model / persisted payload mismatch",
                    field_names,
                    expected_names,
                )

                unsigned = SeveranceStatutorySnapshot(
                    **{
                        key: value
                        for key, value in payload.items()
                        if key != "fingerprint_sha256"
                    },
                    fingerprint_sha256="",
                )

                canonical_bytes = json.dumps(
                    _payload(unsigned),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")

                digest = hashlib.sha256(
                    canonical_bytes
                ).hexdigest()

                assert len(digest) == 64
                assert digest != "a" * 64

                payload["fingerprint_sha256"] = digest

                snapshot = SeveranceStatutorySnapshot(
                    **payload
                )

                assert verify_severance_statutory_snapshot(
                    snapshot
                )

                snapshot_id = await db.scalar(
                    insert(snapshot_table)
                    .values(**payload)
                    .returning(snapshot_table.c.id)
                )

                assert snapshot_id is not None
                await db.flush()

                print(
                    "REAL SHA256 SNAPSHOT INSERT = PASS",
                    flush=True,
                )

        # The payroll fixture and snapshot must be COMMITTED
        # before downgrade. An uncommitted row would not prove
        # protection against historical data loss.

        async with engine.connect() as conn:
            persisted = (
                await conn.execute(
                    select(snapshot_table).where(
                        snapshot_table.c.id == snapshot_id
                    )
                )
            ).mappings().one()

            assert persisted["fingerprint_sha256"] == digest

            persisted_payload = {
                name: persisted[name]
                for name in field_names
            }

            # PostgreSQL JSONB returns arrays as lists.
            persisted_payload["severance_source_ids"] = tuple(
                persisted_payload["severance_source_ids"]
            )

            persisted_snapshot = SeveranceStatutorySnapshot(
                **persisted_payload
            )

            assert verify_severance_statutory_snapshot(
                persisted_snapshot
            )

        print(
            "PERSISTED SHA256 INTEGRITY = PASS",
            flush=True,
        )

        # The inherited require_clone() check already verifies
        # both exact clone naming and local PostgreSQL host.
        import subprocess
        import sys

        downgrade = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "downgrade",
                "13d4a7b8c041",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env=os.environ.copy(),
        )

        assert downgrade.returncode != 0, (
            "DANGEROUS: nonempty downgrade succeeded",
            downgrade.stdout,
            downgrade.stderr,
        )

        output = (
            downgrade.stdout + downgrade.stderr
        ).lower()

        assert (
            "cannot discard historical severance"
            " statutory snapshots"
        ) in output, (
            "Unexpected downgrade failure reason",
            output[-3000:],
        )

        print(
            "NONEMPTY DOWNGRADE = BLOCKED",
            flush=True,
        )

        async with engine.connect() as conn:
            revision = await conn.scalar(
                text("SELECT version_num FROM alembic_version")
            )

            assert revision == "13d4a7b8c063"

            remaining = (
                await conn.execute(
                    select(snapshot_table).where(
                        snapshot_table.c.id == snapshot_id
                    )
                )
            ).mappings().one()

            assert remaining["company_id"] == company.id
            assert remaining["payroll_calculation_id"] == calculation.id
            assert remaining["fingerprint_sha256"] == digest
            assert remaining["severance_source_ids"] == [
                source.id
            ]

            remaining_payload = {
                name: remaining[name]
                for name in field_names
            }
            remaining_payload["severance_source_ids"] = tuple(
                remaining_payload["severance_source_ids"]
            )

            assert verify_severance_statutory_snapshot(
                SeveranceStatutorySnapshot(
                    **remaining_payload
                )
            )

        print(
            "HISTORICAL SNAPSHOT PRESERVED = PASS",
            flush=True,
        )
        print(
            "ALEMBIC REVISION AFTER REJECTED DOWNGRADE = c048",
            flush=True,
        )

    finally:
        await engine.dispose()
