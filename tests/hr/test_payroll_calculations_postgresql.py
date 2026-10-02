from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models.company import Company
from app.models.employee import Employee
from app.models.employee_salary_rate import EmployeeSalaryRate
from app.models.employment_contract import EmploymentContract
from app.models.payroll import (
    PayrollCalculation,
    PayrollCalculationLine,
    PayrollInput,
    PayrollInputSalarySlice,
    PayrollPeriod,
    PayrollPeriodStatus,
)
from app.models.user import User
from app.services.payroll_calculation_service import (
    calculate_payroll_input,
    get_payroll_calculation,
    get_payroll_calculation_for_input,
    list_payroll_calculation_lines,
)


pytestmark = pytest.mark.asyncio


def _database_url():
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


async def test_payroll_calculation_real_postgresql_snapshot_and_idempotency():
    engine = create_async_engine(
        _database_url(),
        pool_pre_ping=True,
    )

    Session = async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    try:
        async with Session() as session:
            async with session.begin():
                (
                    company,
                    user,
                    _employee,
                    contract,
                ) = await _seed_identity(session)

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
                session.add(period)
                await session.flush()

                payroll_input = PayrollInput(
                    company_id=company.id,
                    payroll_period_id=period.id,
                    employment_contract_id=contract.id,
                    scheduled_minutes=9240,
                    worked_minutes=6000,
                    leave_days=0,
                    sick_days=0,
                    manual_adjustment_amount=Decimal(
                        "0.00"
                    ),
                    created_by=user.id,
                )
                session.add(payroll_input)
                await session.flush()

                rate1 = EmployeeSalaryRate(
                    company_id=company.id,
                    employment_contract_id=contract.id,
                    rate_type="hourly",
                    amount=Decimal("20.00"),
                    currency_code="EUR",
                    effective_from=date(2026, 9, 1),
                    effective_to=date(2026, 9, 15),
                    created_by=user.id,
                )

                rate2 = EmployeeSalaryRate(
                    company_id=company.id,
                    employment_contract_id=contract.id,
                    rate_type="hourly",
                    amount=Decimal("30.00"),
                    currency_code="EUR",
                    effective_from=date(2026, 9, 16),
                    effective_to=date(2026, 9, 30),
                    created_by=user.id,
                )

                session.add_all([rate1, rate2])
                await session.flush()

                slice1 = PayrollInputSalarySlice(
                    company_id=company.id,
                    payroll_input_id=payroll_input.id,
                    salary_rate_id=rate1.id,
                    salary_rate_type="hourly",
                    salary_rate_amount=Decimal("20.00"),
                    currency_code="EUR",
                    effective_from=date(2026, 9, 1),
                    effective_to=date(2026, 9, 15),
                    scheduled_minutes=4620,
                    worked_minutes=4800,
                )

                slice2 = PayrollInputSalarySlice(
                    company_id=company.id,
                    payroll_input_id=payroll_input.id,
                    salary_rate_id=rate2.id,
                    salary_rate_type="hourly",
                    salary_rate_amount=Decimal("30.00"),
                    currency_code="EUR",
                    effective_from=date(2026, 9, 16),
                    effective_to=date(2026, 9, 30),
                    scheduled_minutes=4620,
                    worked_minutes=1200,
                )

                session.add_all([slice1, slice2])
                await session.flush()

                assert (
                    slice1.worked_minutes
                    + slice2.worked_minutes
                    == payroll_input.worked_minutes
                )

                assert (
                    slice1.scheduled_minutes
                    + slice2.scheduled_minutes
                    == payroll_input.scheduled_minutes
                )

                calculation = (
                    await calculate_payroll_input(
                        session,
                        company_id=company.id,
                        payroll_input_id=(
                            payroll_input.id
                        ),
                        calculated_by=user.id,
                    )
                )

                await session.flush()

                assert calculation.id is not None
                assert calculation.company_id == company.id
                assert (
                    calculation.payroll_period_id
                    == period.id
                )
                assert (
                    calculation.payroll_input_id
                    == payroll_input.id
                )
                assert (
                    calculation.employment_contract_id
                    == contract.id
                )
                assert (
                    calculation.currency_code
                    == "EUR"
                )

                assert (
                    Decimal(calculation.gross_amount)
                    == Decimal("2200.00")
                )

                lines = (
                    await list_payroll_calculation_lines(
                        session,
                        company_id=company.id,
                        payroll_calculation_id=(
                            calculation.id
                        ),
                    )
                )

                assert len(lines) == 2

                assert (
                    Decimal(lines[0].quantity)
                    == Decimal("80.0000")
                )
                assert (
                    Decimal(lines[0].rate)
                    == Decimal("20.0000")
                )
                assert (
                    Decimal(lines[0].amount)
                    == Decimal("1600.00")
                )
                assert (
                    lines[0].source_salary_rate_id
                    == rate1.id
                )
                assert (
                    lines[0].source_effective_from
                    == date(2026, 9, 1)
                )
                assert (
                    lines[0].source_effective_to
                    == date(2026, 9, 15)
                )

                assert (
                    Decimal(lines[1].quantity)
                    == Decimal("20.0000")
                )
                assert (
                    Decimal(lines[1].rate)
                    == Decimal("30.0000")
                )
                assert (
                    Decimal(lines[1].amount)
                    == Decimal("600.00")
                )
                assert (
                    lines[1].source_salary_rate_id
                    == rate2.id
                )

                persisted = (
                    await get_payroll_calculation(
                        session,
                        company_id=company.id,
                        payroll_calculation_id=(
                            calculation.id
                        ),
                    )
                )

                assert persisted.id == calculation.id
                assert (
                    Decimal(persisted.gross_amount)
                    == Decimal("2200.00")
                )

                by_input = (
                    await get_payroll_calculation_for_input(
                        session,
                        company_id=company.id,
                        payroll_input_id=(
                            payroll_input.id
                        ),
                    )
                )

                assert by_input is not None
                assert by_input.id == calculation.id

                repeated = (
                    await calculate_payroll_input(
                        session,
                        company_id=company.id,
                        payroll_input_id=(
                            payroll_input.id
                        ),
                        calculated_by=user.id,
                    )
                )

                assert repeated.id == calculation.id

                calculation_rows = list(
                    (
                        await session.scalars(
                            select(
                                PayrollCalculation
                            ).where(
                                PayrollCalculation.company_id
                                == company.id,
                                PayrollCalculation.payroll_input_id
                                == payroll_input.id,
                            )
                        )
                    ).all()
                )

                assert len(calculation_rows) == 1

                line_rows = list(
                    (
                        await session.scalars(
                            select(
                                PayrollCalculationLine
                            ).where(
                                PayrollCalculationLine.company_id
                                == company.id,
                                PayrollCalculationLine.payroll_calculation_id
                                == calculation.id,
                            )
                        )
                    ).all()
                )

                assert len(line_rows) == 2

                assert (
                    sum(
                        (
                            Decimal(row.amount)
                            for row in line_rows
                        ),
                        Decimal("0.00"),
                    )
                    == Decimal("2200.00")
                )

                assert (
                    Decimal(calculation.gross_amount)
                    != Decimal("2500.00")
                )

                print(
                    "ASYMMETRIC SNAPSHOT "
                    "4800m@20 + 1200m@30 = "
                    "2200.00 PASS"
                )
                print(
                    "OLD CALENDAR ALLOCATION "
                    "2500.00 = REJECTED"
                )
                print(
                    "SALARY SLICE RECONCILIATION "
                    "= PASS"
                )
                print(
                    "CALCULATION PERSISTENCE = PASS"
                )
                print(
                    "SALARY RATE PROVENANCE = PASS"
                )
                print(
                    "CALCULATION IDEMPOTENCY = PASS"
                )

                await session.rollback()

    finally:
        await engine.dispose()
