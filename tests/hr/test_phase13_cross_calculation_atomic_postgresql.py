"""Real PostgreSQL savepoint acceptance for cross-calculation posting.

Uses a disposable PostgreSQL database and real SQL writes.
Payroll source objects and financial collaborators are substituted.

This verifies transaction atomicity, NOT full payroll financial E2E.
"""

import os
import re
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

import app.services.payroll_cross_calculation_posting as posting


pytestmark = pytest.mark.asyncio


class SourceResult:
    def __init__(self, calculation_id, employee_id=5):
        self.calculation_id = calculation_id
        self.employee_id = employee_id

    def one_or_none(self):
        return (
            SimpleNamespace(
                id=self.calculation_id,
                company_id=1,
                currency_code="UAH",
            ),
            SimpleNamespace(
                employee_id=self.employee_id,
                company_id=1,
            ),
        )


class SourceBoundSession:
    """Only substitute payroll source lookup; SQL remains real."""

    def __init__(self, session, employee_id=5):
        self.session = session
        self.employee_id = employee_id
        self.lookups = 0

    async def execute(self, statement, parameters=None, **kwargs):
        if getattr(statement, "is_select", False):
            self.lookups += 1
            return SourceResult(
                self.lookups,
                self.employee_id,
            )

        return await self.session.execute(
            statement,
            parameters,
            **kwargs,
        )

    async def flush(self):
        return await self.session.flush()

    def begin_nested(self):
        return self.session.begin_nested()


def require_disposable_database():
    raw = os.environ.get("DATABASE_URL")

    if not raw:
        pytest.fail(
            "DATABASE_URL is required from isolated PG runner"
        )

    url = make_url(raw)

    if (
        url.username != "erp_user"
        or url.host not in {"127.0.0.1", "localhost"}
        or not re.fullmatch(
            r"erp_r3_copy_[0-9a-f]{16}",
            url.database or "",
        )
    ):
        pytest.fail(
            "Refusing PostgreSQL outside disposable copy"
        )

    return raw


async def prepare(session):
    await session.execute(text("""
        CREATE TEMP TABLE crosscalc_acceptance_events (
            id integer GENERATED ALWAYS AS IDENTITY,
            calculation_id integer NOT NULL,
            event_type text NOT NULL,
            amount numeric(18,2) NOT NULL
        ) ON COMMIT PRESERVE ROWS
    """))


async def read_events(session):
    rows = await session.execute(text("""
        SELECT calculation_id, event_type, amount
        FROM crosscalc_acceptance_events
        ORDER BY id
    """))

    return [
        (
            int(row[0]),
            row[1],
            str(row[2]),
        )
        for row in rows
    ]


def install_financial_collaborators(
    monkeypatch,
    *,
    fail_second=False,
):
    async def create(
        db,
        *,
        payroll_calculation_id,
        amount,
        **kwargs,
    ):
        await db.execute(
            text("""
                INSERT INTO crosscalc_acceptance_events
                    (calculation_id, event_type, amount)
                VALUES (:calc, 'disbursement', :amount)
            """),
            {
                "calc": payroll_calculation_id,
                "amount": amount,
            },
        )

        return SimpleNamespace(
            id=payroll_calculation_id,
        )

    async def post(
        db,
        *,
        payroll_disbursement_id,
        **kwargs,
    ):
        if fail_second and payroll_disbursement_id == 2:
            raise RuntimeError(
                "Injected second posting failure"
            )

        await db.execute(
            text("""
                INSERT INTO crosscalc_acceptance_events
                    (calculation_id, event_type, amount)
                VALUES (:calc, 'journal', 0.00)
            """),
            {"calc": payroll_disbursement_id},
        )

        return SimpleNamespace(
            id=100 + payroll_disbursement_id,
        )

    monkeypatch.setattr(
        posting,
        "create_payroll_disbursement",
        create,
    )

    monkeypatch.setattr(
        posting,
        "generate_and_post_payroll_disbursement_journal",
        post,
    )


async def execute_batch(db):
    return await posting.post_cross_calculation_payments(
        db,
        company_id=1,
        employee_id=5,
        bank_account_id=10,
        cash_desk_id=None,
        payment_date=date(2026, 11, 16),
        created_by=3,
        request_key="crosscalc-e2e-1",
        items=[
            {
                "payroll_calculation_id": 1,
                "amount": "100.00",
            },
            {
                "payroll_calculation_id": 2,
                "amount": "50.00",
            },
        ],
    )


async def test_postgresql_two_component_atomic_success(
    monkeypatch,
):
    url = require_disposable_database()
    engine = create_async_engine(url)

    try:
        factory = async_sessionmaker(
            engine,
            expire_on_commit=False,
        )

        async with factory() as session:
            async with session.begin():
                await prepare(session)

                install_financial_collaborators(
                    monkeypatch,
                )

                result = await execute_batch(
                    SourceBoundSession(session)
                )

                assert result["amount"] == "150.00"
                assert result["currency_code"] == "UAH"
                assert len(result["components"]) == 2

                events = await read_events(session)

                assert events == [
                    (1, "disbursement", "100.00"),
                    (1, "journal", "0.00"),
                    (2, "disbursement", "50.00"),
                    (2, "journal", "0.00"),
                ]
    finally:
        await engine.dispose()


async def test_postgresql_second_failure_rolls_back_all(
    monkeypatch,
):
    url = require_disposable_database()
    engine = create_async_engine(url)

    try:
        factory = async_sessionmaker(
            engine,
            expire_on_commit=False,
        )

        async with factory() as session:
            async with session.begin():
                await prepare(session)

                install_financial_collaborators(
                    monkeypatch,
                    fail_second=True,
                )

                with pytest.raises(
                    RuntimeError,
                    match="second posting failure",
                ):
                    await execute_batch(
                        SourceBoundSession(session)
                    )

                assert await read_events(session) == []

    finally:
        await engine.dispose()


async def test_postgresql_cross_employee_fails_before_writes(
    monkeypatch,
):
    url = require_disposable_database()
    engine = create_async_engine(url)

    try:
        factory = async_sessionmaker(
            engine,
            expire_on_commit=False,
        )

        async with factory() as session:
            async with session.begin():
                await prepare(session)

                install_financial_collaborators(
                    monkeypatch,
                )

                with pytest.raises(
                    posting.CrossCalculationPostingError,
                    match="Cross-employee",
                ):
                    await execute_batch(
                        SourceBoundSession(
                            session,
                            employee_id=99,
                        )
                    )

                assert await read_events(session) == []

    finally:
        await engine.dispose()
