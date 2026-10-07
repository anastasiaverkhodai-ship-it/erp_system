from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.models
from app.models.company import Company
from app.models.employee import Employee
from app.models.employment_contract import EmploymentContract
from app.models.employee_salary_rate import EmployeeSalaryRate
from app.models.leave_request import (
    LeaveRequest,
    LeaveRequestStatus,
    LeaveType,
)
from app.models.payroll import (
    PayrollCalculation,
    PayrollCalculationLine,
    PayrollInput,
    PayrollInputSalarySlice,
    PayrollPeriod,
    PayrollPeriodStatus,
)
from app.models.payroll_vacation import (
    PayrollVacationCalculation,
    PayrollVacationCalculationSource,
)
from app.models.user import User
from app.services.payroll_calculation_service import (
    calculate_payroll_input,
)
from app.services.payroll_vacation_service import (
    PayrollVacationConflictError,
    PayrollVacationNotFoundError,
    calculate_vacation_pay,
)


def _postgres_url():
    from app.core.config import settings

    url = settings.database_url

    if not url or "postgresql" not in url:
        raise RuntimeError("Real PostgreSQL database_url is required")

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
        if col.default is not None or col.server_default is not None:
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
                "name": f"Payroll Vacation E2E {token}",
            },
        )
    )
    session.add(company)
    await session.flush()

    user_values = _required_values(
        User,
        {
            "email": f"payroll-vacation-{token}@example.com",
            "password_hash": "not-a-real-password",
            "first_name": "Payroll",
            "last_name": "Vacation",
        },
    )

    if "username" in User.__table__.c:
        user_values.setdefault(
            "username",
            f"payroll-vacation-{token}",
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
                "employee_number": f"PV-{token}",
                "first_name": "Payroll",
                "last_name": "Vacation",
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
                "contract_number": f"PV-C-{token}",
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


@pytest.mark.parametrize("cross_month", [False, True])
@pytest.mark.asyncio
async def test_payroll_vacation_real_postgresql_e2e(cross_month):
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
                company, user, _employee, contract = (
                    await _seed_identity(db)
                )

                leave = LeaveRequest(
                    company_id=company.id,
                    employment_contract_id=contract.id,
                    leave_type=LeaveType.ANNUAL,
                    start_date=date(2026, 9, 29) if cross_month else date(2026, 9, 21),
                    end_date=date(2026, 10, 3) if cross_month else date(2026, 9, 25),
                    status=LeaveRequestStatus.APPROVED,
                    requested_by=user.id,
                    approved_by=user.id,
                    approved_at=datetime(
                        2026,
                        9,
                        10,
                        12,
                        0,
                        tzinfo=UTC,
                    ),
                )
                db.add(leave)
                await db.flush()

                vacation = await calculate_vacation_pay(
                    db,
                    company_id=company.id,
                    leave_request_id=leave.id,
                    reference_period_start=date(2025, 9, 1),
                    reference_period_end=date(2026, 8, 31),
                    eligible_earnings=Decimal("73000.00"),
                    eligible_days=Decimal("365.00"),
                    rule_code="VACATION_E2E",
                    rule_version="test-vector-v1",
                    calculated_by=user.id,
                    sources=[
                        {
                            "source_type": "earnings_history",
                            "source_id": 900001,
                            "source_period_start": date(2025, 9, 1),
                            "source_period_end": date(2026, 8, 31),
                            "earnings_amount": Decimal("73000.00"),
                            "eligible_days": Decimal("365.00"),
                            "source_reference": "permanent-pg-e2e",
                        }
                    ],
                )
                await db.flush()

                assert Decimal(vacation.leave_days) == Decimal("5.00")
                assert Decimal(vacation.eligible_earnings) == Decimal(
                    "73000.00"
                )
                assert Decimal(vacation.eligible_days) == Decimal("365.00")
                assert Decimal(vacation.average_daily_amount) == Decimal(
                    "200.000000"
                )
                assert Decimal(vacation.vacation_pay_amount) == Decimal(
                    "1000.00"
                )
                assert vacation.rule_code == "VACATION_E2E"
                assert vacation.rule_version == "test-vector-v1"

                source = (
                    await db.execute(
                        select(PayrollVacationCalculationSource).where(
                            PayrollVacationCalculationSource.company_id
                            == company.id,
                            PayrollVacationCalculationSource.vacation_calculation_id
                            == vacation.id,
                        )
                    )
                ).scalar_one()

                assert source.source_type == "earnings_history"
                assert source.source_id == 900001
                assert Decimal(source.earnings_amount) == Decimal(
                    "73000.00"
                )
                assert Decimal(source.eligible_days) == Decimal("365.00")
                assert source.source_reference == "permanent-pg-e2e"

                same = await calculate_vacation_pay(
                    db,
                    company_id=company.id,
                    leave_request_id=leave.id,
                    reference_period_start=date(2025, 9, 1),
                    reference_period_end=date(2026, 8, 31),
                    eligible_earnings=Decimal("73000.00"),
                    eligible_days=Decimal("365.00"),
                    rule_code="VACATION_E2E",
                    rule_version="test-vector-v1",
                    calculated_by=user.id,
                    sources=[
                        {
                            "source_type": "earnings_history",
                            "source_id": 900001,
                            "source_period_start": date(2025, 9, 1),
                            "source_period_end": date(2026, 8, 31),
                            "earnings_amount": Decimal("73000.00"),
                            "eligible_days": Decimal("365.00"),
                            "source_reference": "permanent-pg-e2e",
                        }
                    ],
                )

                assert same.id == vacation.id

                vacation_count = (
                    await db.execute(
                        select(func.count())
                        .select_from(PayrollVacationCalculation)
                        .where(
                            PayrollVacationCalculation.company_id
                            == company.id,
                            PayrollVacationCalculation.leave_request_id
                            == leave.id,
                        )
                    )
                ).scalar_one()

                assert vacation_count == 1

                from types import SimpleNamespace
                from app.services.payroll_vacation_service import derive_vacation_pay_lines
                combined = Decimal(0)
                for begin, end in ((date(2026,9,1),date(2026,9,30)), (date(2026,10,1),date(2026,10,31))):
                    rows = await derive_vacation_pay_lines(db,company_id=company.id,
                        payroll_input=SimpleNamespace(employment_contract_id=contract.id),
                        period=SimpleNamespace(start_date=begin,end_date=end))
                    combined += sum(row['amount'] for row in rows)
                assert combined == vacation.vacation_pay_amount, 'Cross-month payroll repeats full leave amount'

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
                    scheduled_minutes=0,
                    worked_minutes=0,
                    leave_days=5,
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
                    salary_rate_type=salary_rate.rate_type,
                    salary_rate_amount=salary_rate.amount,
                    currency_code=salary_rate.currency_code,
                    effective_from=date(2026, 9, 1),
                    effective_to=date(2026, 9, 30),
                    scheduled_minutes=0,
                    worked_minutes=0,
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

                vacation_line = (
                    await db.execute(
                        select(PayrollCalculationLine).where(
                            PayrollCalculationLine.company_id
                            == company.id,
                            PayrollCalculationLine.payroll_calculation_id
                            == calculation.id,
                            PayrollCalculationLine.line_type
                            == "vacation_pay",
                        )
                    )
                ).scalar_one()

                assert (
                    vacation_line.source_vacation_calculation_id
                    == vacation.id
                )
                assert Decimal(vacation_line.quantity) == Decimal(2 if cross_month else 5)
                assert Decimal(vacation_line.rate) == Decimal("200.0000")
                assert Decimal(vacation_line.amount) == Decimal("400.00" if cross_month else "1000.00")
                assert Decimal(calculation.gross_amount) == Decimal(
                    "400.00" if cross_month else "1000.00"
                )

                foreign_company = Company(
                    **_required_values(
                        Company,
                        {
                            "name": (
                                f"Payroll Vacation Foreign "
                                f"{uuid4().hex[:10]}"
                            ),
                        },
                    )
                )
                db.add(foreign_company)
                await db.flush()

                with pytest.raises(PayrollVacationNotFoundError):
                    async with db.begin_nested():
                        await calculate_vacation_pay(
                            db,
                            company_id=foreign_company.id,
                            leave_request_id=leave.id,
                            reference_period_start=date(2025, 9, 1),
                            reference_period_end=date(2026, 8, 31),
                            eligible_earnings=Decimal("73000.00"),
                            eligible_days=Decimal("365.00"),
                            rule_code="VACATION_E2E",
                            rule_version="test-vector-v1",
                            calculated_by=user.id,
                        )

                pending_leave = LeaveRequest(
                    company_id=company.id,
                    employment_contract_id=contract.id,
                    leave_type=LeaveType.ANNUAL,
                    start_date=date(2026, 10, 5),
                    end_date=date(2026, 10, 6),
                    status=LeaveRequestStatus.PENDING,
                    requested_by=user.id,
                )
                db.add(pending_leave)
                await db.flush()

                with pytest.raises(PayrollVacationConflictError):
                    async with db.begin_nested():
                        await calculate_vacation_pay(
                            db,
                            company_id=company.id,
                            leave_request_id=pending_leave.id,
                            reference_period_start=date(2025, 10, 1),
                            reference_period_end=date(2026, 9, 30),
                            eligible_earnings=Decimal("73000.00"),
                            eligible_days=Decimal("365.00"),
                            rule_code="VACATION_E2E",
                            rule_version="test-vector-v1",
                            calculated_by=user.id,
                        )

                await db.rollback()

            async with Session() as verify:
                remaining = (
                    await verify.execute(
                        select(func.count())
                        .select_from(PayrollVacationCalculation)
                        .where(
                            PayrollVacationCalculation.rule_code
                            == "VACATION_E2E"
                        )
                    )
                ).scalar_one()

                assert remaining == 0

    finally:
        await engine.dispose()
