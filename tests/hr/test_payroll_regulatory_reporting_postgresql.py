from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.models.company import Company
from app.models.employee import Employee
from app.models.employment_contract import EmploymentContract
from app.models.payroll import (
    PayrollCalculation,
    PayrollInput,
    PayrollPeriod,
)
from app.models.payroll_regulatory_report import (
    PayrollRegulatoryReport,
    PayrollRegulatoryReportKind,
    PayrollRegulatoryReportRow,
    PayrollRegulatoryReportStatus,
)
from app.models.user import User
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportNotFoundError,
    create_payroll_regulatory_report,
    generate_payroll_regulatory_report,
    list_payroll_regulatory_report_rows,
)


def _postgres_url() -> str:
    url = settings.database_url

    if not url or "postgresql" not in url:
        pytest.fail(
            "Real PostgreSQL database_url is required; "
            "13.14 E2E must not skip"
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


async def _seed_identity(db: AsyncSession):
    token = uuid4().hex[:10]

    company = Company(
        **_required_values(
            Company,
            {
                "name": f"Regulatory E2E {token}",
            },
        )
    )
    db.add(company)
    await db.flush()

    user_values = _required_values(
        User,
        {
            "email": f"regulatory-{token}@example.com",
            "password_hash": "not-a-real-password",
            "first_name": "Regulatory",
            "last_name": "E2E",
        },
    )

    if "username" in User.__table__.c:
        user_values.setdefault(
            "username",
            f"regulatory-{token}",
        )

    if "hashed_password" in User.__table__.c:
        user_values.setdefault(
            "hashed_password",
            "not-a-real-password",
        )

    if "is_active" in User.__table__.c:
        user_values["is_active"] = True

    actor = User(**user_values)
    db.add(actor)
    await db.flush()

    employee = Employee(
        **_required_values(
            Employee,
            {
                "company_id": company.id,
                "employee_number": f"RR-{token}",
                "first_name": "Regulatory",
                "last_name": "Employee",
                "hire_date": date(2026, 1, 1),
                "created_by": actor.id,
                "status": "active",
            },
        )
    )
    db.add(employee)
    await db.flush()

    contract = EmploymentContract(
        **_required_values(
            EmploymentContract,
            {
                "company_id": company.id,
                "employee_id": employee.id,
                "contract_number": f"RR-C-{token}",
                "contract_type": "standard",
                "work_arrangement": "full_time",
                "start_date": date(2026, 1, 1),
                "end_date": None,
                "status": "active",
                "created_by": actor.id,
            },
        )
    )
    db.add(contract)
    await db.flush()

    return company, actor, employee, contract


def _payroll_input_values(
    *,
    company_id: int,
    period_id: int,
    contract_id: int,
    actor_id: int,
):
    values = {}

    aliases = {
        "company_id": company_id,
        "payroll_period_id": period_id,
        "employment_contract_id": contract_id,
        "contract_id": contract_id,
        "created_by": actor_id,
        "updated_by": actor_id,
        "actor_id": actor_id,
    }

    for name, value in aliases.items():
        if name in PayrollInput.__table__.c:
            values[name] = value

    for col in PayrollInput.__table__.columns:
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

        if col.name.endswith("_id"):
            pytest.fail(
                "Unresolved required PayrollInput FK: "
                f"{col.name}"
            )

        try:
            python_type = col.type.python_type
        except Exception:
            python_type = None

        if python_type is str:
            values[col.name] = "e2e"
        elif python_type is int:
            values[col.name] = 1
        elif python_type is bool:
            values[col.name] = True
        elif python_type is date:
            values[col.name] = date(2026, 10, 1)
        elif python_type is Decimal:
            values[col.name] = Decimal("10000.00")
        else:
            pytest.fail(
                "Unresolved required PayrollInput field: "
                f"{col.name}"
            )

    return values


async def _seed_payroll(db: AsyncSession):
    company, actor, employee, contract = (
        await _seed_identity(db)
    )

    period = PayrollPeriod(
        company_id=company.id,
        year=2026,
        month=10,
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 31),
        status="draft",
        created_by=actor.id,
    )
    db.add(period)
    await db.flush()

    payroll_input = PayrollInput(
        **_payroll_input_values(
            company_id=company.id,
            period_id=period.id,
            contract_id=contract.id,
            actor_id=actor.id,
        )
    )
    db.add(payroll_input)
    await db.flush()

    calculation = PayrollCalculation(
        company_id=company.id,
        payroll_period_id=period.id,
        payroll_input_id=payroll_input.id,
        employment_contract_id=contract.id,
        status="calculated",
        currency_code="UAH",
        gross_amount=Decimal("10000.00"),
        calculated_by=actor.id,
    )
    db.add(calculation)
    await db.flush()

    return (
        company,
        actor,
        employee,
        contract,
        period,
        payroll_input,
        calculation,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind_value",
    ["d1", "4df", "d5", "d6"],
)
async def test_regulatory_report_real_postgresql_e2e(
    kind_value: str,
):
    url = _postgres_url()

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

            (
                company,
                actor,
                _employee,
                contract,
                period,
                _payroll_input,
                calculation,
            ) = await _seed_payroll(db)

            before_reports = await db.scalar(
                select(
                    func.count(
                        PayrollRegulatoryReport.id
                    )
                ).where(
                    PayrollRegulatoryReport.company_id
                    == company.id
                )
            )

            assert before_reports == 0

            report = (
                await create_payroll_regulatory_report(
                    db,
                    company_id=company.id,
                    payroll_period_id=period.id,
                    report_kind=kind_value,
                    created_by=actor.id,
                )
            )

            await db.flush()

            assert report.id is not None
            assert report.company_id == company.id
            assert report.payroll_period_id == period.id
            assert report.report_kind == kind_value
            assert report.revision == 1
            assert report.status == (
                PayrollRegulatoryReportStatus.DRAFT.value
            )

            generated = (
                await generate_payroll_regulatory_report(
                    db,
                    company_id=company.id,
                    payroll_regulatory_report_id=report.id,
                    generated_by=actor.id,
                )
            )

            await db.flush()

            assert generated.id == report.id
            assert generated.status == (
                PayrollRegulatoryReportStatus
                .GENERATED.value
            )
            assert generated.generated_at is not None
            assert generated.generated_by == actor.id

            rows = (
                await list_payroll_regulatory_report_rows(
                    db,
                    company_id=company.id,
                    payroll_regulatory_report_id=report.id,
                )
            )

            assert len(rows) == 1

            row = rows[0]

            assert row.company_id == company.id
            assert (
                row.payroll_regulatory_report_id
                == report.id
            )
            assert (
                row.payroll_calculation_id
                == calculation.id
            )
            assert (
                row.employment_contract_id
                == contract.id
            )

            first_row_ids = [item.id for item in rows]

            generated_again = (
                await generate_payroll_regulatory_report(
                    db,
                    company_id=company.id,
                    payroll_regulatory_report_id=report.id,
                    generated_by=actor.id,
                )
            )

            await db.flush()

            assert generated_again.id == report.id

            rows_again = (
                await list_payroll_regulatory_report_rows(
                    db,
                    company_id=company.id,
                    payroll_regulatory_report_id=report.id,
                )
            )

            assert len(rows_again) == 1
            assert [
                item.id
                for item in rows_again
            ] == first_row_ids

            revision_2 = (
                await create_payroll_regulatory_report(
                    db,
                    company_id=company.id,
                    payroll_period_id=period.id,
                    report_kind=kind_value,
                    created_by=actor.id,
                )
            )

            await db.flush()

            assert revision_2.revision == 2
            assert revision_2.id != report.id

            with pytest.raises(
                PayrollRegulatoryReportNotFoundError
            ):
                await (
                    list_payroll_regulatory_report_rows(
                        db,
                        company_id=company.id + 1000000000,
                        payroll_regulatory_report_id=report.id,
                    )
                )

            report_id = report.id
            revision_2_id = revision_2.id

            await db.rollback()

        async with Session() as verify_db:
            report_count = await verify_db.scalar(
                select(
                    func.count(
                        PayrollRegulatoryReport.id
                    )
                ).where(
                    PayrollRegulatoryReport.id.in_(
                        [
                            report_id,
                            revision_2_id,
                        ]
                    )
                )
            )

            row_count = await verify_db.scalar(
                select(
                    func.count(
                        PayrollRegulatoryReportRow.id
                    )
                ).where(
                    PayrollRegulatoryReportRow
                    .payroll_regulatory_report_id
                    == report_id
                )
            )

            assert report_count == 0
            assert row_count == 0

    finally:
        await engine.dispose()


def test_regulatory_service_transaction_ownership():
    from pathlib import Path

    source = Path(
        "app/services/"
        "payroll_regulatory_report_service.py"
    ).read_text()

    for forbidden in (
        "await db.commit(",
        "await db.rollback(",
        "await session.commit(",
        "await session.rollback(",
    ):
        assert forbidden not in source
