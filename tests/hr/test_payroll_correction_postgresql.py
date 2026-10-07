from __future__ import annotations

import os
from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import delete, select

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models.payroll import (
    PayrollCalculation,
    PayrollInput,
    PayrollPeriod,
)
from app.models.payroll_correction import PayrollCorrection
from app.services.payroll_correction_service import (
    PayrollCorrectionNotFoundError,
    PayrollCorrectionSourceStateError,
    create_payroll_correction_link,
)

from app.models.employment_contract import EmploymentContract
from app.models.employee_salary_rate import EmployeeSalaryRate
from app.models.employee import Employee
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
async def test_payroll_correction_revision_contract_real_postgresql():
    url = _postgres_url()

    if not url:
        pytest.fail(
            "Canonical PostgreSQL URL is required; "
            "13.13 real PostgreSQL E2E must not skip"
        )

    engine = create_async_engine(
        url,
        echo=False,
    )

    Session = async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    try:
        async with Session() as db:
            bind = db.get_bind()

            if bind.dialect.name != "postgresql":
                pytest.fail(
                    "Real PostgreSQL required; "
                    f"active dialect={bind.dialect.name!r}"
                )

            identity = await _seed_identity(db)

            if isinstance(identity, dict):
                company = (
                    identity.get("company")
                    or identity.get("company_obj")
                )
                actor = (
                    identity.get("actor")
                    or identity.get("user")
                )
                contract = (
                    identity.get("contract")
                    or identity.get("employment_contract")
                )
            elif isinstance(identity, tuple):
                objects = list(identity)

                company = next(
                    (
                        obj for obj in objects
                        if obj.__class__.__name__ == "Company"
                    ),
                    None,
                )

                actor = next(
                    (
                        obj for obj in objects
                        if obj.__class__.__name__ == "User"
                    ),
                    None,
                )

                contract = next(
                    (
                        obj for obj in objects
                        if obj.__class__.__name__
                        == "EmploymentContract"
                    ),
                    None,
                )
            else:
                raise AssertionError(
                    "Unsupported _seed_identity return type: "
                    f"{type(identity)!r}"
                )

            assert company is not None
            assert actor is not None
            assert contract is not None

            company_id = company.id
            actor_id = actor.id

            period = PayrollPeriod(
                **_required_values(
                    PayrollPeriod,
                    {
                        "company_id": company_id,
                        "year": 2098,
                        "month": 1,
                        "start_date": date(2098, 1, 1),
                        "end_date": date(2098, 1, 31),
                        "status": PayrollPeriodStatus.DRAFT,
                        "created_by": actor_id,
                    },
                )
            )

            db.add(period)
            await db.flush()

            payroll_input = PayrollInput(
                **_required_values(
                    PayrollInput,
                    {
                        "company_id": company_id,
                        "payroll_period_id": period.id,
                        "employment_contract_id": contract.id,
                        "created_by": actor_id,
                    },
                )
            )

            db.add(payroll_input)
            await db.flush()

            original = PayrollCalculation(
                **_required_values(
                    PayrollCalculation,
                    {
                        "company_id": company_id,
                        "payroll_period_id": period.id,
                        "revision": 1,
                        "payroll_input_id": payroll_input.id,
                        "employment_contract_id": contract.id,
                        "status": "calculated",
                        "currency_code": "UAH",
                        "gross_amount": Decimal("10000.00"),
                        "calculated_by": actor_id,
                    },
                )
            )

            db.add(original)
            await db.flush()

            replacement = PayrollCalculation(
                **_required_values(
                    PayrollCalculation,
                    {
                        "company_id": company_id,
                        "payroll_period_id": period.id,
                        "revision": 2,
                        "payroll_input_id": payroll_input.id,
                        "employment_contract_id": contract.id,
                        "status": original.status,
                        "currency_code": original.currency_code,
                        "gross_amount": Decimal("10100.00"),
                        "calculated_by": actor_id,
                    },
                )
            )

            db.add(replacement)
            await db.flush()

            request_key = (
                f"pg-e2e-correction-"
                f"{original.id}-{replacement.id}"
            )

            correction = await create_payroll_correction_link(
                db,
                company_id=company_id,
                original_payroll_calculation_id=original.id,
                replacement_payroll_calculation_id=replacement.id,
                request_key=request_key,
                reason="13.13 PostgreSQL correction E2E",
                created_by=actor_id,
            )

            assert correction.id is not None

            assert (
                correction.original_payroll_calculation_id
                == original.id
            )

            assert (
                correction.replacement_payroll_calculation_id
                == replacement.id
            )

            assert replacement.revision == 2

            same = await create_payroll_correction_link(
                db,
                company_id=company_id,
                original_payroll_calculation_id=original.id,
                replacement_payroll_calculation_id=replacement.id,
                request_key=request_key,
                reason="13.13 PostgreSQL correction E2E",
                created_by=actor_id,
            )

            assert same.id == correction.id

            correction_count = await db.scalar(
                select(
                    func.count(PayrollCorrection.id)
                ).where(
                    PayrollCorrection.company_id
                    == company_id,
                    PayrollCorrection.request_key
                    == request_key,
                )
            )

            assert correction_count == 1

            with pytest.raises(
                PayrollCorrectionSourceStateError
            ):
                await create_payroll_correction_link(
                    db,
                    company_id=company_id,
                    original_payroll_calculation_id=original.id,
                    replacement_payroll_calculation_id=replacement.id,
                    request_key=request_key,
                    reason="different payload",
                    created_by=actor_id,
                )

            wrong_revision = PayrollCalculation(
                **_required_values(
                    PayrollCalculation,
                    {
                        "company_id": company_id,
                        "payroll_period_id": period.id,
                        "revision": 4,
                        "payroll_input_id": payroll_input.id,
                        "employment_contract_id": contract.id,
                        "status": original.status,
                        "currency_code": original.currency_code,
                        "gross_amount": Decimal("10200.00"),
                        "calculated_by": actor_id,
                    },
                )
            )

            db.add(wrong_revision)
            await db.flush()

            with pytest.raises(
                PayrollCorrectionSourceStateError
            ):
                await create_payroll_correction_link(
                    db,
                    company_id=company_id,
                    original_payroll_calculation_id=original.id,
                    replacement_payroll_calculation_id=wrong_revision.id,
                    request_key=request_key + "-wrong",
                    reason="invalid revision chain",
                    created_by=actor_id,
                )

            with pytest.raises(
                PayrollCorrectionNotFoundError
            ):
                await create_payroll_correction_link(
                    db,
                    company_id=company_id,
                    original_payroll_calculation_id=2147483647,
                    replacement_payroll_calculation_id=replacement.id,
                    request_key=request_key + "-missing",
                    reason="missing original",
                    created_by=actor_id,
                )

            original_id = original.id
            replacement_id = replacement.id

            print("COMPANY =", company_id)
            print("CONTRACT =", contract.id)
            print("ORIGINAL =", original_id)
            print("REPLACEMENT =", replacement_id)
            print("REQUEST IDEMPOTENCY = PASS")
            print("NEXT REVISION GUARD = PASS")

            await db.rollback()

            leaked_correction = await db.scalar(
                select(
                    func.count(PayrollCorrection.id)
                ).where(
                    PayrollCorrection.request_key
                    == request_key
                )
            )

            leaked_original = await db.scalar(
                select(
                    func.count(PayrollCalculation.id)
                ).where(
                    PayrollCalculation.id
                    == original_id
                )
            )

            leaked_replacement = await db.scalar(
                select(
                    func.count(PayrollCalculation.id)
                ).where(
                    PayrollCalculation.id
                    == replacement_id
                )
            )

            assert leaked_correction == 0
            assert leaked_original == 0
            assert leaked_replacement == 0

            print("ROLLBACK ZERO-LEAK = PASS")

    finally:
        await engine.dispose()
