from __future__ import annotations

from test_employee_foundation import employee_engine

from datetime import UTC, date, datetime
from decimal import Decimal
import inspect
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.models
from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.bank_account import BankAccount
from app.models.company import Company
from app.models.employee import Employee
from app.models.employee_salary_rate import EmployeeSalaryRate
from app.models.employment_contract import EmploymentContract
from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.models.payment import Payment
from app.models.payroll import (
    PayrollInput,
    PayrollInputSalarySlice,
    PayrollPeriod,
    PayrollPeriodStatus,
)
from app.models.payroll_deduction import (
    PayrollDeductionInstruction,
)
from app.models.payroll_deduction_result import (
    PayrollDeductionResult,
    PayrollDeductionResultLine,
)
from app.models.payroll_statutory import (
    PayrollStatutoryComponent,
)
from app.models.user import User

from app.schemas.payroll_deduction import (
    PayrollDeductionInstructionCreate,
)

from app.services.account_types import (
    AccountNormalBalance,
    AccountType,
)
from app.services.payroll_advance_service import (
    create_payroll_advance,
    generate_and_post_payroll_advance_journal,
)
from app.services.payroll_calculation_service import (
    calculate_payroll_input,
)
from app.services.payroll_deduction_service import (
    create_payroll_deduction_instruction,
)
from app.services.payroll_deduction_result_service import (
    calculate_payroll_deduction_result,
)
from app.services.payroll_deduction_accounting_service import (
    generate_and_post_payroll_deduction_journal,
    reverse_payroll_deduction_journal,
)
from app.services.payroll_disbursement_service import (
    PayrollDisbursementSourceStateError,
    create_payroll_disbursement,
)
from app.services.payroll_payslip_service import (
    generate_payroll_payslip,
)
from app.services.payroll_statutory_service import (
    calculate_payroll_statutory_result,
    create_statutory_rate,
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
                "name": f"Payroll Disbursement E2E {token}",
            },
        )
    )
    session.add(company)
    await session.flush()

    user_values = _required_values(
        User,
        {
            "email": f"payroll-disbursement-{token}@example.com",
            "password_hash": "not-a-real-password",
            "first_name": "Payroll",
            "last_name": "Disbursement",
        },
    )

    if "username" in User.__table__.c:
        user_values.setdefault(
            "username",
            f"payroll-disbursement-{token}",
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
                "employee_number": f"PD-{token}",
                "first_name": "Payroll",
                "last_name": "Disbursement",
                "payment_iban": "UA123456789012345678901234567",
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
                "contract_number": f"PD-C-{token}",
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

def _call_kwargs(fn, values):
    parameters = inspect.signature(fn).parameters

    return {
        key: value
        for key, value in values.items()
        if key in parameters
    }


@pytest.mark.asyncio
async def test_payroll_deduction_real_postgresql_e2e(employee_engine):
    engine = employee_engine

    Session = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    try:
        async with Session() as db:
            async with db.begin():
                company, user, employee, contract = (
                    await _seed_identity(db)
                )

                period = PayrollPeriod(
                    company_id=company.id,
                    year=2026,
                    month=9,
                    start_date=date(2026, 9, 1),
                    end_date=date(2026, 9, 30),
                    status=PayrollPeriodStatus.DRAFT,
                    created_by=user.id,
                )
                db.add(period)

                db.add_all(
                    [
                        AccountingPeriod(
                            company_id=company.id,
                            year=2026,
                            month=9,
                            start_date=date(2026, 9, 1),
                            end_date=date(2026, 9, 30),
                            status="open",
                            is_locked=False,
                        ),
                        AccountingPeriod(
                            company_id=company.id,
                            year=2026,
                            month=10,
                            start_date=date(2026, 10, 1),
                            end_date=date(2026, 10, 31),
                            status="open",
                            is_locked=False,
                        ),
                    ]
                )

                payroll_payable = Account(
                    company_id=company.id,
                    code="66",
                    name="Payroll payable",
                    account_type=AccountType.LIABILITY,
                    normal_balance=AccountNormalBalance.CREDIT,
                    is_postable=True,
                    is_system=True,
                    is_active=True,
                )

                deduction_payable = Account(
                    company_id=company.id,
                    code="68",
                    name="Payroll deduction payable",
                    account_type=AccountType.LIABILITY,
                    normal_balance=AccountNormalBalance.CREDIT,
                    is_postable=True,
                    is_system=True,
                    is_active=True,
                )

                bank_gl = Account(
                    company_id=company.id,
                    code="311",
                    name="Current bank account",
                    account_type=AccountType.ASSET,
                    normal_balance=AccountNormalBalance.DEBIT,
                    is_postable=True,
                    is_system=False,
                    is_active=True,
                )

                db.add_all(
                    [
                        payroll_payable,
                        deduction_payable,
                        bank_gl,
                    ]
                )
                await db.flush()

                bank = BankAccount(
                    company_id=company.id,
                    name="Payroll Deduction E2E Bank",
                    account_number=(
                        f"UA-DEDUCTION-{uuid4().hex[:10]}"
                    ),
                    currency_code="UAH",
                    accounting_account_id=bank_gl.id,
                    is_active=True,
                )
                db.add(bank)
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

                advance = await create_payroll_advance(
                    db,
                    company_id=company.id,
                    payroll_period_id=period.id,
                    employment_contract_id=contract.id,
                    bank_account_id=bank.id,
                    advance_percentage=Decimal("40"),
                    calculation_base_amount=Decimal(
                        "10000.00"
                    ),
                    minimum_due_amount=Decimal(
                        "1000.00"
                    ),
                    currency_code="UAH",
                    payment_date=date(2026, 9, 15),
                    created_by=user.id,
                )
                await db.flush()

                assert Decimal(
                    advance.paid_amount
                ) == Decimal("4000.00")

                advance_journal = (
                    await generate_and_post_payroll_advance_journal(
                        db,
                        company_id=company.id,
                        payroll_advance_id=advance.id,
                        created_by=user.id,
                    )
                )
                await db.flush()

                assert advance_journal.status == "posted"


                print("ADVANCE CREATED BEFORE FINALIZATION = PASS")

                period.status = PayrollPeriodStatus.FINALIZED
                period.finalized_by = user.id
                period.finalized_at = datetime(
                    2026,
                    10,
                    1,
                    12,
                    0,
                    tzinfo=UTC,
                )
                await db.flush()

                calculation = await calculate_payroll_input(
                    db,
                    company_id=company.id,
                    payroll_input_id=payroll_input.id,
                    calculated_by=user.id,
                )
                await db.flush()

                assert Decimal(
                    calculation.gross_amount
                ) == Decimal("10000.00")

                gross_before = Decimal(
                    calculation.gross_amount
                )

                for component, rate in (
                    (
                        PayrollStatutoryComponent.PERSONAL_INCOME_TAX,
                        Decimal("0.18"),
                    ),
                    (
                        PayrollStatutoryComponent.MILITARY_LEVY,
                        Decimal("0.05"),
                    ),
                    (
                        PayrollStatutoryComponent.UNIFIED_SOCIAL_CONTRIBUTION,
                        Decimal("0.22"),
                    ),
                ):
                    await create_statutory_rate(
                        db,
                        company_id=company.id,
                        component=component,
                        rate=rate,
                        effective_from=date(2026, 1, 1),
                        effective_to=None,
                        actor_user_id=user.id,
                    )

                statutory = (
                    await calculate_payroll_statutory_result(
                        db,
                        company_id=company.id,
                        payroll_calculation_id=calculation.id,
                        actor_user_id=user.id,
                    )
                )
                await db.flush()

                assert Decimal(
                    statutory.net_amount
                ) == Decimal("7700.00")

                statutory_net_before = Decimal(
                    statutory.net_amount
                )

                fixed_payload = (
                    PayrollDeductionInstructionCreate(
                        employment_contract_id=contract.id,
                        deduction_type="court_order",
                        method="fixed",
                        fixed_amount=Decimal("500.00"),
                        percentage=None,
                        currency_code="UAH",
                        priority=10,
                        effective_from=date(2026, 9, 1),
                        effective_to=date(2026, 9, 30),
                        request_key=(
                            f"ded-fixed-{uuid4().hex[:12]}"
                        ),
                        source_reference="PG-E2E-FIXED",
                    )
                )

                fixed = (
                    await create_payroll_deduction_instruction(
                        db,
                        company_id=company.id,
                        data=fixed_payload,
                        created_by=user.id,
                    )
                )

                fixed_repeat = (
                    await create_payroll_deduction_instruction(
                        db,
                        company_id=company.id,
                        data=fixed_payload,
                        created_by=user.id,
                    )
                )

                assert fixed_repeat.id == fixed.id

                percentage_payload = (
                    PayrollDeductionInstructionCreate(
                        employment_contract_id=contract.id,
                        deduction_type="union_fee",
                        method="percentage",
                        fixed_amount=None,
                        percentage=Decimal("2.00"),
                        currency_code="UAH",
                        priority=20,
                        effective_from=date(2026, 9, 1),
                        effective_to=date(2026, 9, 30),
                        request_key=(
                            f"ded-pct-{uuid4().hex[:12]}"
                        ),
                        source_reference="PG-E2E-PERCENT",
                    )
                )

                percentage = (
                    await create_payroll_deduction_instruction(
                        db,
                        company_id=company.id,
                        data=percentage_payload,
                        created_by=user.id,
                    )
                )

                future_payload = (
                    PayrollDeductionInstructionCreate(
                        employment_contract_id=contract.id,
                        deduction_type="future_only",
                        method="fixed",
                        fixed_amount=Decimal("999.00"),
                        percentage=None,
                        currency_code="UAH",
                        priority=1,
                        effective_from=date(2026, 10, 1),
                        effective_to=None,
                        request_key=(
                            f"ded-future-{uuid4().hex[:12]}"
                        ),
                        source_reference="PG-E2E-FUTURE",
                    )
                )

                await create_payroll_deduction_instruction(
                    db,
                    company_id=company.id,
                    data=future_payload,
                    created_by=user.id,
                )

                # Phase 13: legal authorization is required before any
                # deduction can enter payroll results or accounting.
                from app.services.payroll_deduction_result_service import (
                    PayrollDeductionResultConflictError,
                )

                with pytest.raises(
                    PayrollDeductionResultConflictError,
                    match="not authorized for automatic payroll application",
                ):
                    await calculate_payroll_deduction_result(
                        db,
                        company_id=company.id,
                        payroll_calculation_id=calculation.id,
                        calculated_by=user.id,
                    )

                await db.flush()

                result_count = await db.scalar(
                    select(func.count(PayrollDeductionResult.id)).where(
                        PayrollDeductionResult.company_id == company.id,
                        PayrollDeductionResult.payroll_calculation_id
                        == calculation.id,
                    )
                )

                journal_count = await db.scalar(
                    select(func.count(JournalEntry.id)).where(
                        JournalEntry.company_id == company.id,
                        JournalEntry.payroll_deduction_result_id.is_not(None),
                    )
                )

                assert result_count == 0
                assert journal_count == 0

                await db.refresh(statutory)
                await db.refresh(calculation)

                assert Decimal(statutory.net_amount) == Decimal("7700.00")
                assert Decimal(calculation.gross_amount) == Decimal("10000.00")

                print("UNAUTHORIZED DEDUCTION = BLOCKED")
                print("DEDUCTION RESULT ROWS = 0")
                print("DEDUCTION JOURNAL ROWS = 0")
                print("STATUTORY NET 7700.00 = UNCHANGED")
                print("GROSS 10000.00 = UNCHANGED")

                await db.rollback()

    finally:
        pass  # employee_engine fixture owns engine disposal
