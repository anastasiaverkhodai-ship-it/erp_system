from __future__ import annotations

from uuid import uuid4
from app.services.payroll_calculation_service import (
    calculate_payroll_input,
)
from app.models.employment_contract import EmploymentContract
from app.models.employee_salary_rate import EmployeeSalaryRate
from app.models.employee import Employee

import os
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.models
from app.models.company import Company
from app.models.payroll import (
    PayrollCalculation,
    PayrollInput,
    PayrollInputSalarySlice,
    PayrollPeriod,
    PayrollPeriodStatus,
)
from app.models.payroll_statutory import (
    PayrollStatutoryComponent,
    PayrollStatutoryRate,
    PayrollStatutoryResult,
    PayrollStatutoryResultLine,
)
from app.models.user import User
from app.services.payroll_statutory_service import (
    calculate_payroll_statutory_result,
    create_statutory_rate,
    get_statutory_result,
    get_statutory_result_for_calculation,
    list_statutory_result_lines,
)


def _postgres_url():
    from app.core.config import settings

    url = settings.database_url

    if not url or "postgresql" not in url:
        raise RuntimeError(
            "Real PostgreSQL database_url is required"
        )

    if url.startswith("postgresql://"):
        url = (
            "postgresql+asyncpg://"
            + url[len("postgresql://"):]
        )
    elif url.startswith("postgresql+psycopg://"):
        url = (
            "postgresql+asyncpg://"
            + url[len("postgresql+psycopg://"):]
        )

    return url




def _required_values(model, overrides):
    values = dict(overrides)

    for col in model.__table__.columns:
        if col.name in values:
            continue

        if col.primary_key and col.autoincrement:
            continue

        if col.nullable:
            continue

        if (
            col.default is not None
            or col.server_default is not None
        ):
            continue

        name = col.name.lower()

        if name.endswith("_id"):
            continue

        try:
            python_type = col.type.python_type
        except Exception:
            python_type = None

        if python_type is str:
            values[col.name] = f"e2e-{col.name}"
        elif python_type is int:
            values[col.name] = 1
        elif python_type is bool:
            values[col.name] = True
        elif python_type is date:
            values[col.name] = date(2026, 1, 1)
        elif python_type is Decimal:
            values[col.name] = Decimal("1.00")

    return values


async def _seed_identity(session):
    token = uuid4().hex[:10]

    company = Company(
        **_required_values(
            Company,
            {
                "name": (
                    f"Payroll Calculation E2E {token}"
                ),
            },
        )
    )
    session.add(company)
    await session.flush()

    user_values = _required_values(
        User,
        {
            "email": (
                f"payroll-calc-{token}@example.com"
            ),
            "password_hash": "not-a-real-password",
            "first_name": "Payroll",
            "last_name": "Calculation",
        },
    )

    if "username" in User.__table__.c:
        user_values.setdefault(
            "username",
            f"payroll-calc-{token}",
        )

    if "hashed_password" in User.__table__.c:
        user_values.setdefault(
            "hashed_password",
            "not-a-real-password",
        )

    if "is_active" in User.__table__.c:
        user_values["is_active"] = True

    user = User(**user_values)
    session.add(user)
    await session.flush()

    employee = Employee(
        **_required_values(
            Employee,
            {
                "company_id": company.id,
                "employee_number": f"PC-{token}",
                "first_name": "Payroll",
                "last_name": "Calculation",
                "hire_date": date(2026, 1, 1),
                "created_by": user.id,
                "status": "active",
            },
        )
    )
    session.add(employee)
    await session.flush()

    contract = EmploymentContract(
        **_required_values(
            EmploymentContract,
            {
                "company_id": company.id,
                "employee_id": employee.id,
                "contract_number": f"PC-C-{token}",
                "contract_type": "standard",
                "work_arrangement": "full_time",
                "start_date": date(2026, 1, 1),
                "end_date": None,
                "status": "active",
                "created_by": user.id,
            },
        )
    )
    session.add(contract)
    await session.flush()

    return company, user, employee, contract


@pytest.mark.asyncio
async def test_payroll_statutory_real_postgresql_snapshot_and_idempotency():
    engine = create_async_engine(
        _postgres_url(),
        pool_pre_ping=True,
    )

    Session = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    try:
        async with Session() as db:
            async with db.begin():
                (
                    company,
                    user,
                    _employee,
                    contract,
                ) = await _seed_identity(db)

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
                        2026,
                        10,
                        1,
                        12,
                        0,
                        tzinfo=UTC,
                    ),
                )
                db.add(period)
                await db.flush()

                payroll_input = PayrollInput(
                    company_id=company.id,
                    payroll_period_id=period.id,
                    employment_contract_id=contract.id,
                    scheduled_minutes=6000,
                    worked_minutes=6000,
                    leave_days=0,
                    sick_days=0,
                    manual_adjustment_amount=Decimal("0.00"),
                    created_by=user.id,
                )
                db.add(payroll_input)
                await db.flush()

                salary_rate = EmployeeSalaryRate(
                    company_id=company.id,
                    employment_contract_id=contract.id,
                    rate_type="hourly",
                    amount=Decimal("100.00"),
                    currency_code="UAH",
                    effective_from=date(2026, 9, 1),
                    effective_to=date(2026, 9, 30),
                    created_by=user.id,
                )
                db.add(salary_rate)
                await db.flush()

                salary_slice = PayrollInputSalarySlice(
                    company_id=company.id,
                    payroll_input_id=payroll_input.id,
                    salary_rate_id=salary_rate.id,
                    salary_rate_type="hourly",
                    salary_rate_amount=Decimal("100.00"),
                    currency_code="UAH",
                    effective_from=date(2026, 9, 1),
                    effective_to=date(2026, 9, 30),
                    scheduled_minutes=6000,
                    worked_minutes=6000,
                )
                db.add(salary_slice)
                await db.flush()

                calculation = await calculate_payroll_input(
                    db,
                    company_id=company.id,
                    payroll_input_id=payroll_input.id,
                    calculated_by=user.id,
                )
                await db.flush()

                assert calculation.id is not None
                assert (
                    Decimal(calculation.gross_amount)
                    == Decimal("10000.00")
                )
                assert calculation.currency_code == "UAH"

                effective_date = period.end_date

                pit = await create_statutory_rate(
                    db,
                    company_id=company.id,
                    component=(
                        PayrollStatutoryComponent
                        .PERSONAL_INCOME_TAX
                    ),
                    rate=Decimal("0.18"),
                    effective_from=date(2026, 1, 1),
                    effective_to=None,
                    actor_user_id=user.id,
                )

                military = await create_statutory_rate(
                    db,
                    company_id=company.id,
                    component=(
                        PayrollStatutoryComponent
                        .MILITARY_LEVY
                    ),
                    rate=Decimal("0.05"),
                    effective_from=date(2026, 1, 1),
                    effective_to=None,
                    actor_user_id=user.id,
                )

                usc = await create_statutory_rate(
                    db,
                    company_id=company.id,
                    component=(
                        PayrollStatutoryComponent
                        .UNIFIED_SOCIAL_CONTRIBUTION
                    ),
                    rate=Decimal("0.22"),
                    effective_from=date(2026, 1, 1),
                    effective_to=None,
                    actor_user_id=user.id,
                )

                first = (
                    await calculate_payroll_statutory_result(
                        db,
                        company_id=company.id,
                        payroll_calculation_id=calculation.id,
                        actor_user_id=user.id,
                    )
                )

                await db.flush()

                assert first.company_id == company.id
                assert (
                    first.payroll_calculation_id
                    == calculation.id
                )
                assert first.currency_code == "UAH"

                assert (
                    Decimal(first.gross_amount)
                    == Decimal("10000.00")
                )
                assert (
                    Decimal(
                        first.employee_withholding_amount
                    )
                    == Decimal("2300.00")
                )
                assert (
                    Decimal(
                        first.employer_contribution_amount
                    )
                    == Decimal("2200.00")
                )
                assert (
                    Decimal(first.net_amount)
                    == Decimal("7700.00")
                )

                lines = (
                    await list_statutory_result_lines(
                        db,
                        company_id=company.id,
                        statutory_result_id=first.id,
                    )
                )

                assert len(lines) == 3

                by_component = {
                    line.component: line
                    for line in lines
                }

                pit_line = by_component[
                    PayrollStatutoryComponent
                    .PERSONAL_INCOME_TAX.value
                ]

                military_line = by_component[
                    PayrollStatutoryComponent
                    .MILITARY_LEVY.value
                ]

                usc_line = by_component[
                    PayrollStatutoryComponent
                    .UNIFIED_SOCIAL_CONTRIBUTION.value
                ]

                assert (
                    Decimal(pit_line.base_amount)
                    == Decimal("10000.00")
                )
                assert (
                    Decimal(pit_line.rate)
                    == Decimal("0.18000000")
                )
                assert (
                    Decimal(pit_line.amount)
                    == Decimal("1800.00")
                )
                assert pit_line.source_rate_id == pit.id

                assert (
                    Decimal(military_line.base_amount)
                    == Decimal("10000.00")
                )
                assert (
                    Decimal(military_line.rate)
                    == Decimal("0.05000000")
                )
                assert (
                    Decimal(military_line.amount)
                    == Decimal("500.00")
                )
                assert (
                    military_line.source_rate_id
                    == military.id
                )

                assert (
                    Decimal(usc_line.base_amount)
                    == Decimal("10000.00")
                )
                assert (
                    Decimal(usc_line.rate)
                    == Decimal("0.22000000")
                )
                assert (
                    Decimal(usc_line.amount)
                    == Decimal("2200.00")
                )
                assert usc_line.source_rate_id == usc.id

                for line in lines:
                    assert line.currency_code == "UAH"
                    assert (
                        line.rate_effective_from
                        == date(2026, 1, 1)
                    )
                    assert line.rate_effective_to is None

                second = (
                    await calculate_payroll_statutory_result(
                        db,
                        company_id=company.id,
                        payroll_calculation_id=(
                            calculation.id
                        ),
                        actor_user_id=user.id,
                    )
                )

                assert second.id == first.id

                stored = await get_statutory_result(
                    db,
                    company_id=company.id,
                    statutory_result_id=first.id,
                )

                assert stored.id == first.id
                assert (
                    Decimal(stored.net_amount)
                    == Decimal("7700.00")
                )

                by_calculation = (
                    await get_statutory_result_for_calculation(
                        db,
                        company_id=company.id,
                        payroll_calculation_id=(
                            calculation.id
                        ),
                    )
                )

                assert by_calculation is not None
                assert by_calculation.id == first.id

                result_rows = list(
                    (
                        await db.scalars(
                            select(
                                PayrollStatutoryResult
                            ).where(
                                PayrollStatutoryResult
                                .company_id
                                == company.id,
                                PayrollStatutoryResult
                                .payroll_calculation_id
                                == calculation.id,
                            )
                        )
                    ).all()
                )

                assert len(result_rows) == 1

                line_rows = list(
                    (
                        await db.scalars(
                            select(
                                PayrollStatutoryResultLine
                            ).where(
                                PayrollStatutoryResultLine
                                .company_id
                                == company.id,
                                PayrollStatutoryResultLine
                                .payroll_statutory_result_id
                                == first.id,
                            )
                        )
                    ).all()
                )

                assert len(line_rows) == 3

                assert (
                    sum(
                        (
                            Decimal(row.amount)
                            for row in line_rows
                            if row.component
                            in {
                                PayrollStatutoryComponent
                                .PERSONAL_INCOME_TAX.value,
                                PayrollStatutoryComponent
                                .MILITARY_LEVY.value,
                            }
                        ),
                        Decimal("0.00"),
                    )
                    == Decimal("2300.00")
                )

                assert (
                    Decimal(first.gross_amount)
                    - Decimal(
                        first.employee_withholding_amount
                    )
                    == Decimal(first.net_amount)
                )

                await db.rollback()

    finally:
        await engine.dispose()

    print("STATUTORY GROSS = 10000.00 PASS")
    print("PERSONAL INCOME TAX VECTOR = 1800.00 PASS")
    print("MILITARY LEVY VECTOR = 500.00 PASS")
    print("EMPLOYEE WITHHOLDING = 2300.00 PASS")
    print("EMPLOYER CONTRIBUTION = 2200.00 PASS")
    print("NET PAY = 7700.00 PASS")
    print("RATE PROVENANCE SNAPSHOT = PASS")
    print("STATUTORY IDEMPOTENCY = PASS")
