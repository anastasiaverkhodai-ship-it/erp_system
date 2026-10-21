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


async def test_severance_snapshot_service_persistence_e2e():
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


                from dataclasses import replace
                from datetime import date as calendar_date

                from app.services.payroll_severance_snapshot_integration_service import (
                    preview_severance_statutory_snapshot,
                )
                from app.services.payroll_severance_snapshot_persistence_service import (
                    SeveranceSnapshotPersistenceError,
                    persist_severance_statutory_snapshot,
                    snapshot_from_record,
                )
                from app.services.payroll_severance_tax_classification import (
                    SeveranceTaxPolicy,
                )
                from app.services.payroll_severance_statutory_snapshot import (
                    _fingerprint,
                    _payload,
                    verify_severance_statutory_snapshot,
                )

                policy = SeveranceTaxPolicy(
                    code="TEST-ONLY-B4-POSTGRESQL",
                    valid_from=calendar_date(2026, 9, 1),
                    valid_to=calendar_date(2026, 9, 30),
                    source_reference="SYNTHETIC-TEST-NOT-LEGAL-APPROVAL",
                    personal_income_tax_included=True,
                    military_levy_included=True,
                    unified_social_contribution_included=False,
                    income_code_4df="127",
                )

                snapshot = await preview_severance_statutory_snapshot(
                    db,
                    company_id=company.id,
                    payroll_calculation_id=calculation.id,
                    income_date=calendar_date(2026, 9, 30),
                    policies=[policy],
                )

                assert snapshot.company_id == company.id
                assert snapshot.payroll_calculation_id == calculation.id
                assert snapshot.severance_source_ids == (source.id,)
                assert snapshot.severance_gross == Decimal("12000.00")
                assert snapshot.ordinary_gross == Decimal("1040.00")
                assert snapshot.total_gross == Decimal("13040.00")
                assert verify_severance_statutory_snapshot(snapshot)

                # 1. Proof of caller-owned rollback:
                # an INSERT inside a savepoint must disappear on rollback.
                savepoint = await db.begin_nested()

                try:
                    transient = await persist_severance_statutory_snapshot(
                        db,
                        snapshot=snapshot,
                    )
                    assert transient.id is not None
                    assert snapshot_from_record(transient) == snapshot

                finally:
                    if savepoint.is_active:
                        await savepoint.rollback()

                count_after_rollback = await db.scalar(
                    select(func.count(snapshot_table.c.id)).where(
                        snapshot_table.c.company_id == company.id,
                        snapshot_table.c.payroll_calculation_id
                        == calculation.id,
                    )
                )

                assert count_after_rollback == 0
                print("SERVICE SAVEPOINT ROLLBACK = PASS", flush=True)

                # 2. Genuine positive insert with the production service.
                first = await persist_severance_statutory_snapshot(
                    db,
                    snapshot=snapshot,
                )

                assert first.id is not None
                first_id = first.id

                assert first.fingerprint_sha256 == snapshot.fingerprint_sha256
                assert snapshot_from_record(first) == snapshot
                print("SERVICE INSERT + SHA256 = PASS", flush=True)

                # 3. Repeating identical evidence cannot create another row.
                second = await persist_severance_statutory_snapshot(
                    db,
                    snapshot=snapshot,
                )

                assert second.id == first_id

                count_after_repeat = await db.scalar(
                    select(func.count(snapshot_table.c.id)).where(
                        snapshot_table.c.company_id == company.id,
                        snapshot_table.c.payroll_calculation_id
                        == calculation.id,
                    )
                )

                assert count_after_repeat == 1
                print("SERVICE IDEMPOTENT RETRY = PASS", flush=True)

                # 4. Changed but internally valid SHA256 evidence
                # must not replace or silently reuse historical evidence.
                changed_unsigned = replace(
                    snapshot,
                    source_reference="ALTERED-SYNTHETIC-EVIDENCE",
                    fingerprint_sha256="",
                )

                changed = replace(
                    changed_unsigned,
                    fingerprint_sha256=_fingerprint(
                        _payload(changed_unsigned)
                    ),
                )

                assert verify_severance_statutory_snapshot(changed)

                with pytest.raises(
                    SeveranceSnapshotPersistenceError,
                    match="conflicts with evidence",
                ):
                    await persist_severance_statutory_snapshot(
                        db,
                        snapshot=changed,
                    )

                stored = await db.scalar(
                    select(snapshot_table.c.fingerprint_sha256).where(
                        snapshot_table.c.id == first_id
                    )
                )

                assert stored == snapshot.fingerprint_sha256
                print("CHANGED SNAPSHOT = REJECTED", flush=True)

                # 5. Cross-company ownership must be rejected.
                forged_unsigned = replace(
                    snapshot,
                    company_id=company.id + 99999999,
                    fingerprint_sha256="",
                )

                forged = replace(
                    forged_unsigned,
                    fingerprint_sha256=_fingerprint(
                        _payload(forged_unsigned)
                    ),
                )

                assert verify_severance_statutory_snapshot(forged)

                with pytest.raises(
                    SeveranceSnapshotPersistenceError,
                    match="not found in company",
                ):
                    await persist_severance_statutory_snapshot(
                        db,
                        snapshot=forged,
                    )

                assert (
                    await db.scalar(
                        select(func.count(snapshot_table.c.id)).where(
                            snapshot_table.c.company_id == company.id,
                            snapshot_table.c.payroll_calculation_id
                            == calculation.id,
                        )
                    )
                ) == 1

                print("CROSS-COMPANY SERVICE CALL = REJECTED", flush=True)

                # Transaction is committed only when this caller-owned
                # db.begin() context exits normally.

            async with Session() as verify_session:
                persisted_record = await verify_session.scalar(
                    select(PayrollSeveranceStatutoryRecord).where(
                        PayrollSeveranceStatutoryRecord.company_id
                        == company.id,
                        PayrollSeveranceStatutoryRecord.payroll_calculation_id
                        == calculation.id,
                    )
                )

                assert persisted_record is not None
                assert persisted_record.id == first_id

                roundtrip = snapshot_from_record(persisted_record)
                assert roundtrip == snapshot

                # Idempotent retry across a NEW session.
                async with verify_session.begin_nested():
                    third = await persist_severance_statutory_snapshot(
                        verify_session,
                        snapshot=snapshot,
                    )
                    assert third.id == first_id

            print(
                "COMMITTED RELOAD + CROSS-SESSION IDEMPOTENCY = PASS",
                flush=True,
            )

    finally:
        await engine.dispose()
