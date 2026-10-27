"""Real migration and historical severance protection on a clone."""

import os
import re
import subprocess
import sys

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.models.employment_event import EmploymentEvent
from app.models.payroll import PayrollInput, PayrollPeriod
from app.models.payroll_termination_severance import (
    PayrollTerminationSeverance as Severance,
)
from app.services.payroll_termination_severance_preflight import (
    preflight_termination_severance,
)

from test_payroll_vacation_postgresql import _seed_identity


pytestmark = pytest.mark.asyncio


def isolated_url():
    assert os.getenv("RUN_POSTGRES_E2E") == "1"

    url = make_url(str(settings.database_url))

    assert re.fullmatch(
        r"erp_r3_copy_[0-9a-f]{16}",
        url.database or "",
    ), "Refusing to use the original ERP database"

    assert url.host in ("localhost", "127.0.0.1", "::1")

    return str(settings.database_url)


def alembic(*arguments, success=True):
    # DATABASE_URL was replaced by the isolated clone runner.
    isolated_url()

    result = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        capture_output=True,
        text=True,
    )

    if success:
        assert result.returncode == 0, (
            result.stderr[-3000:]
        )

    return result


async def test_severance_migration_and_history_e2e(empty_migration_database):
    url = isolated_url()

    # Wrapper upgrades the clone to the current Alembic HEAD.
    # Prove empty-history downgrade to c039, then restore the current HEAD.
    assert alembic("current").returncode == 0

    alembic("downgrade", "13d4a7b8c039")
    alembic("upgrade", "head")
    alembic("check")

    engine = create_async_engine(
        url,
        pool_pre_ping=True,
    )

    token = uuid4().hex[:10]

    try:
        async with AsyncSession(
            engine, expire_on_commit=False
        ) as db:

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
                status="draft",
                created_by=user.id,
            )

            db.add(period)
            await db.flush()

            payroll_input = PayrollInput(
                company_id=company.id,
                payroll_period_id=period.id,
                employment_contract_id=contract.id,
                created_by=user.id,
            )

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

            db.add_all([payroll_input, event])
            await db.flush()

            preflight = await preflight_termination_severance(
                db,
                company_id=company.id,
                payroll_input_id=payroll_input.id,
            )

            assert preflight.termination_event_id == event.id
            assert preflight.policy.category == "average_months"
            assert preflight.policy.multiplier == 1

            values = {
                "company_id": company.id,
                "payroll_input_id": payroll_input.id,
                "employment_contract_id": contract.id,
                "termination_event_id": event.id,
                "revision": 1,
                "supersedes_id": None,
                "request_key": f"severance-{token}",
                "termination_date": date(2026, 9, 30),
                "order_number": event.order_number,
                "policy_ground": "redundancy",
                "policy_category": "average_months",
                "labour_code_provision": "40.1",
                "source_reference": "Verified termination order",
                "multiplier": 1,
                "basis_as_of": date(2026, 9, 30),
                "basis_reference": "Verified monthly average basis",
                "base_amount": Decimal("12000.00"),
                "gross_amount": Decimal("12000.00"),
                "currency_code": "UAH",
                "created_by": user.id,
            }

            source = Severance(**values)
            db.add(source)
            await db.flush()

            source_id = source.id

            await db.commit()

        # Demonstrate protection after the source has committed.
        async with AsyncSession(
            engine, expire_on_commit=False
        ) as db:

            # UPDATE/DELETE must be rejected by our immutable
            # source trigger, with its explicit check-violation code.
            for statement in (
                "UPDATE payroll_termination_severances "
                "SET gross_amount = 1 WHERE id=:id",
                "DELETE FROM payroll_termination_severances "
                "WHERE id=:id",
            ):
                with pytest.raises(IntegrityError) as caught:
                    async with db.begin_nested():
                        await db.execute(
                            text(statement),
                            {"id": source_id},
                        )

                assert (
                    getattr(caught.value.orig, "sqlstate", None)
                    == "23514"
                )
                assert (
                    "Historical severance source is immutable"
                    in str(caught.value)
                )

            # c041 introduces an FK from payroll calculation lines.
            # PostgreSQL can reject TRUNCATE at its FK guard before
            # the source's BEFORE TRUNCATE trigger runs.
            # Accept ONLY expected SQLSTATE 0A000 (FK restriction)
            # or 23514 (immutable-source trigger).
            from sqlalchemy.exc import DBAPIError

            with pytest.raises(DBAPIError) as caught:
                async with db.begin_nested():
                    await db.execute(
                        text("TRUNCATE payroll_termination_severances")
                    )

            sqlstate = getattr(
                caught.value.orig, "sqlstate", None
            )

            assert sqlstate in {"0A000", "23514"}, sqlstate

            if sqlstate == "0A000":
                assert (
                    "cannot truncate a table referenced"
                    in str(caught.value).lower()
                )
            else:
                assert (
                    "Historical severance source is immutable"
                    in str(caught.value)
                )

            original = await db.get(Severance, source_id)

            assert original is not None
            assert original.gross_amount == Decimal("12000.00")

            negative_cases = (
                {"policy_category": "manual_review"},
                {"gross_amount": Decimal("-1.00")},
                {"revision": 2, "supersedes_id": None},
                {"company_id": company.id + 1000000},
            )

            for index, changes in enumerate(negative_cases):
                candidate = dict(values)
                candidate.update(changes)
                candidate["request_key"] = (
                    f"invalid-{token}-{index}"
                )

                with pytest.raises(IntegrityError):
                    async with db.begin_nested():
                        db.add(Severance(**candidate))
                        await db.flush()

            # Original source remains immutable; correction is a
            # distinct documented revision.
            corrected = dict(values)
            corrected.update({
                "request_key": f"revision-2-{token}",
                "revision": 2,
                "supersedes_id": source_id,
                "base_amount": Decimal("12500.00"),
                "gross_amount": Decimal("12500.00"),
                "basis_reference": "Revised documented average",
            })

            db.add(Severance(**corrected))
            await db.flush()
            await db.commit()

        async with AsyncSession(engine) as db:
            count = await db.scalar(
                select(func.count(Severance.id)).where(
                    Severance.company_id == company.id
                )
            )

            assert count == 2

            old = await db.get(Severance, source_id)
            assert old.gross_amount == Decimal("12000.00")

    finally:
        await engine.dispose()

    # A populated severance history must block destructive downgrade.
    result = alembic(
        "downgrade",
        "13d4a7b8c039",
        success=False,
    )

    assert result.returncode != 0

    assert (
        "Cannot drop documented severance history"
        in result.stderr
    )

    # A failed transactional downgrade must preserve the original head.
    # create_async_engine is already imported at module scope.
    verify_engine = create_async_engine(url)

    try:
        async with verify_engine.connect() as conn:
            revision = await conn.scalar(
                text("SELECT version_num FROM alembic_version")
            )

            assert revision == "13d4a7b8c063"
    finally:
        await verify_engine.dispose()

    print("ISOLATED MIGRATION ROUNDTRIP = PASS")
    print("REAL POSTGRESQL SOURCE INSERT = PASS")
    print("DOCUMENTED TERMINATION PREFLIGHT = PASS")
    print("UPDATE / DELETE / TRUNCATE = BLOCKED")
    print("INVALID FK / CHECK = BLOCKED")
    print("REVISION 2 = INSERTED")
    print("ORIGINAL SOURCE = UNCHANGED")
    print("POPULATED DOWNGRADE = BLOCKED")
    print("POSTGRESQL SKIPS = 0")
