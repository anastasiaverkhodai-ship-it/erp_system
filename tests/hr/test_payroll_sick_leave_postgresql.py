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
from app.models.payroll_sick_leave import (
    PayrollSickLeaveCalculation,
    PayrollSickLeaveCalculationSource,
)
from app.models.user import User
from app.services.payroll_calculation_service import (
    calculate_payroll_input,
)
from app.services.payroll_sick_leave_service import (
    PayrollSickLeaveConflictError,
    PayrollSickLeaveNotFoundError,
    calculate_sick_leave_pay,
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



@pytest.mark.asyncio
async def test_payroll_sick_leave_real_postgresql_e2e():
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
                    leave_type=LeaveType.SICK,
                    start_date=date(2026, 9, 21),
                    end_date=date(2026, 9, 30),
                    status=LeaveRequestStatus.APPROVED,
                    requested_by=user.id,
                    approved_by=user.id,
                    approved_at=datetime(
                        2026,
                        9,
                        20,
                        12,
                        0,
                        tzinfo=UTC,
                    ),
                )
                db.add(leave)
                await db.flush()

                sick = await calculate_sick_leave_pay(
                    db,
                    company_id=company.id,
                    leave_request_id=leave.id,
                    benefit_case_code="ORDINARY_TEMPORARY_INCAPACITY",
                    reference_period_start=date(2025, 9, 1),
                    reference_period_end=date(2026, 8, 31),
                    eligible_earnings=Decimal("36500.00"),
                    eligible_days=Decimal("365.00"),
                    insurance_service_months=120,
                    benefit_percent=Decimal("80.00"),
                    limited_service_rule_applied=False,
                    employer_days=Decimal("5.00"),
                    insurer_days=Decimal("5.00"),
                    rule_code="SICK_E2E",
                    rule_version="test-vector-v1",
                    calculated_by=user.id,
                    sources=[
                        {
                            "source_type": "earnings_history",
                            "source_id": 910001,
                            "source_period_start": date(2025, 9, 1),
                            "source_period_end": date(2026, 8, 31),
                            "earnings_amount": Decimal("36500.00"),
                            "eligible_days": Decimal("365.00"),
                            "source_reference": "permanent-pg-e2e",
                        },
                        {
                            "source_type": "insurance_service_evidence",
                            "source_id": 910002,
                            "earnings_amount": Decimal("0.00"),
                            "eligible_days": Decimal("0.00"),
                            "source_reference": "120-month-test-vector",
                        },
                    ],
                )

                await db.flush()

                assert Decimal(sick.sick_days) == Decimal("10.00")
                assert Decimal(sick.average_daily_amount) == Decimal(
                    "100.000000"
                )
                assert Decimal(sick.benefit_percent) == Decimal(
                    "80.0000"
                )
                assert Decimal(sick.daily_benefit_amount) == Decimal(
                    "80.000000"
                )
                assert Decimal(sick.employer_days) == Decimal("5.00")
                assert Decimal(sick.insurer_days) == Decimal("5.00")
                assert Decimal(sick.employer_amount) == Decimal(
                    "400.00"
                )
                assert Decimal(sick.insurer_amount) == Decimal(
                    "400.00"
                )
                assert Decimal(sick.sick_pay_amount) == Decimal(
                    "800.00"
                )
                assert sick.insurance_service_months == 120
                assert sick.limited_service_rule_applied is False
                assert sick.rule_code == "SICK_E2E"
                assert sick.rule_version == "test-vector-v1"

                sources = list(
                    (
                        await db.execute(
                            select(
                                PayrollSickLeaveCalculationSource
                            )
                            .where(
                                PayrollSickLeaveCalculationSource.company_id
                                == company.id,
                                PayrollSickLeaveCalculationSource
                                .sick_leave_calculation_id
                                == sick.id,
                            )
                            .order_by(
                                PayrollSickLeaveCalculationSource.id
                            )
                        )
                    ).scalars().all()
                )

                assert len(sources) == 2
                assert sources[0].source_type == "earnings_history"
                assert sources[1].source_type == (
                    "insurance_service_evidence"
                )

                same = await calculate_sick_leave_pay(
                    db,
                    company_id=company.id,
                    leave_request_id=leave.id,
                    benefit_case_code="ORDINARY_TEMPORARY_INCAPACITY",
                    reference_period_start=date(2025, 9, 1),
                    reference_period_end=date(2026, 8, 31),
                    eligible_earnings=Decimal("36500.00"),
                    eligible_days=Decimal("365.00"),
                    insurance_service_months=120,
                    benefit_percent=Decimal("80.00"),
                    limited_service_rule_applied=False,
                    employer_days=Decimal("5.00"),
                    insurer_days=Decimal("5.00"),
                    rule_code="SICK_E2E",
                    rule_version="test-vector-v1",
                    calculated_by=user.id,
                )

                assert same.id == sick.id

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
                    leave_days=0,
                    sick_days=10,
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

                sick_line = (
                    await db.execute(
                        select(PayrollCalculationLine).where(
                            PayrollCalculationLine.company_id
                            == company.id,
                            PayrollCalculationLine.payroll_calculation_id
                            == calculation.id,
                            PayrollCalculationLine.line_type
                            == "sick_pay",
                        )
                    )
                ).scalar_one()

                assert (
                    sick_line.source_sick_leave_calculation_id
                    == sick.id
                )
                assert Decimal(sick_line.quantity) == Decimal(
                    "10.0000"
                )
                assert Decimal(sick_line.rate) == Decimal(
                    "80.0000"
                )
                assert Decimal(sick_line.amount) == Decimal(
                    "800.00"
                )
                assert Decimal(calculation.gross_amount) == Decimal(
                    "800.00"
                )

                foreign_company = Company(
                    **_required_values(
                        Company,
                        {
                            "name": (
                                f"Payroll Sick Foreign "
                                f"{uuid4().hex[:10]}"
                            ),
                        },
                    )
                )
                db.add(foreign_company)
                await db.flush()

                with pytest.raises(
                    PayrollSickLeaveNotFoundError
                ):
                    async with db.begin_nested():
                        await calculate_sick_leave_pay(
                            db,
                            company_id=foreign_company.id,
                            leave_request_id=leave.id,
                            benefit_case_code="TEST",
                            reference_period_start=date(
                                2025, 9, 1
                            ),
                            reference_period_end=date(
                                2026, 8, 31
                            ),
                            eligible_earnings=Decimal(
                                "36500.00"
                            ),
                            eligible_days=Decimal("365.00"),
                            insurance_service_months=120,
                            benefit_percent=Decimal("80.00"),
                            limited_service_rule_applied=False,
                            employer_days=Decimal("5.00"),
                            insurer_days=Decimal("5.00"),
                            rule_code="SICK_E2E",
                            rule_version="test-vector-v1",
                            calculated_by=user.id,
                        )

                pending_leave = LeaveRequest(
                    company_id=company.id,
                    employment_contract_id=contract.id,
                    leave_type=LeaveType.SICK,
                    start_date=date(2026, 10, 5),
                    end_date=date(2026, 10, 6),
                    status=LeaveRequestStatus.PENDING,
                    requested_by=user.id,
                )
                db.add(pending_leave)
                await db.flush()

                with pytest.raises(
                    PayrollSickLeaveConflictError
                ):
                    async with db.begin_nested():
                        await calculate_sick_leave_pay(
                            db,
                            company_id=company.id,
                            leave_request_id=pending_leave.id,
                            benefit_case_code="TEST",
                            reference_period_start=date(
                                2025, 10, 1
                            ),
                            reference_period_end=date(
                                2026, 9, 30
                            ),
                            eligible_earnings=Decimal(
                                "36500.00"
                            ),
                            eligible_days=Decimal("365.00"),
                            insurance_service_months=120,
                            benefit_percent=Decimal("80.00"),
                            limited_service_rule_applied=False,
                            employer_days=Decimal("1.00"),
                            insurer_days=Decimal("1.00"),
                            rule_code="SICK_E2E",
                            rule_version="test-vector-v1",
                            calculated_by=user.id,
                        )

                await db.rollback()

            async with Session() as verify:
                remaining = (
                    await verify.execute(
                        select(func.count())
                        .select_from(PayrollSickLeaveCalculation)
                        .where(
                            PayrollSickLeaveCalculation.rule_code
                            == "SICK_E2E"
                        )
                    )
                ).scalar_one()

                assert remaining == 0

    finally:
        await engine.dispose()
