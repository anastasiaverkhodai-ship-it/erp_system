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
from app.models.payroll_disbursement import PayrollDisbursement
from app.models.payroll_statutory import (
    PayrollStatutoryComponent,
)
from app.models.user import User
from app.services.account_types import (
    AccountNormalBalance,
    AccountType,
)
from app.services.payroll_calculation_service import (
    calculate_payroll_input,
)
from app.services.payroll_disbursement_service import (
    PayrollDisbursementNotFoundError,
    create_payroll_disbursement,
    generate_and_post_payroll_disbursement_journal,
    get_payroll_disbursement,
    get_payroll_disbursement_journal,
    reverse_payroll_disbursement_journal,
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


@pytest.mark.asyncio
async def test_payroll_disbursement_real_postgresql_e2e():
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
                    employee,
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

                db.add(
                    AccountingPeriod(
                        company_id=company.id,
                        year=2026,
                        month=9,
                        start_date=date(2026, 9, 1),
                        end_date=date(2026, 9, 30),
                        status="open",
                        is_locked=False,
                    )
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

                db.add_all([payroll_payable, bank_gl])
                await db.flush()

                bank = BankAccount(
                    company_id=company.id,
                    name="Payroll UAH Bank",
                    account_number="UA-PAYROLL-E2E",
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
                await db.flush()

                calculation = await calculate_payroll_input(
                    db,
                    company_id=company.id,
                    payroll_input_id=payroll_input.id,
                    calculated_by=user.id,
                )
                await db.flush()

                assert Decimal(calculation.gross_amount) == Decimal(
                    "10000.00"
                )
                assert calculation.currency_code == "UAH"

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

                assert Decimal(statutory.net_amount) == Decimal(
                    "7700.00"
                )

                payment_date = date(2026, 9, 30)

                disbursement = await create_payroll_disbursement(
                    db,
                    company_id=company.id,
                    payroll_calculation_id=calculation.id,
                    bank_account_id=bank.id,
                    payment_date=payment_date,
                    created_by=user.id,
                )
                await db.flush()

                assert disbursement.company_id == company.id
                assert (
                    disbursement.payroll_calculation_id
                    == calculation.id
                )
                assert disbursement.employee_id == employee.id
                assert disbursement.bank_account_id == bank.id
                assert (
                    disbursement.employee_iban_snapshot
                    == employee.payment_iban
                )
                assert Decimal(disbursement.amount) == Decimal(
                    "7700.00"
                )
                assert disbursement.currency_code == "UAH"
                assert disbursement.payment_date == payment_date

                original_disbursement_id = disbursement.id

                repeated_disbursement = (
                    await create_payroll_disbursement(
                        db,
                        company_id=company.id,
                        payroll_calculation_id=calculation.id,
                        bank_account_id=bank.id,
                        payment_date=payment_date,
                        created_by=user.id,
                    )
                )

                assert (
                    repeated_disbursement.id
                    == original_disbursement_id
                )

                stored = await get_payroll_disbursement(
                    db,
                    company_id=company.id,
                    payroll_calculation_id=calculation.id,
                )
                assert stored is not None
                assert stored.id == original_disbursement_id

                disbursement_rows = (
                    await db.execute(
                        select(PayrollDisbursement).where(
                            PayrollDisbursement.company_id
                            == company.id,
                            PayrollDisbursement.payroll_calculation_id
                            == calculation.id,
                        )
                    )
                ).scalars().all()

                assert len(disbursement_rows) == 1

                journal = (
                    await generate_and_post_payroll_disbursement_journal(
                        db,
                        company_id=company.id,
                        payroll_disbursement_id=disbursement.id,
                        created_by=user.id,
                    )
                )
                await db.flush()

                assert journal.status == "posted"
                assert journal.entry_date == payment_date
                assert (
                    journal.payroll_disbursement_id
                    == disbursement.id
                )
                assert journal.payroll_calculation_id is None
                assert journal.reversal_of_id is None

                original_journal_id = journal.id

                loaded_journal = (
                    await get_payroll_disbursement_journal(
                        db,
                        company_id=company.id,
                        payroll_disbursement_id=disbursement.id,
                    )
                )

                assert loaded_journal is not None
                assert loaded_journal.id == original_journal_id

                repeated_journal = (
                    await generate_and_post_payroll_disbursement_journal(
                        db,
                        company_id=company.id,
                        payroll_disbursement_id=disbursement.id,
                        created_by=user.id,
                    )
                )

                assert repeated_journal.id == original_journal_id

                originals = (
                    await db.execute(
                        select(JournalEntry).where(
                            JournalEntry.company_id == company.id,
                            JournalEntry.payroll_disbursement_id
                            == disbursement.id,
                            JournalEntry.reversal_of_id.is_(None),
                        )
                    )
                ).scalars().all()

                assert len(originals) == 1

                lines = (
                    await db.execute(
                        select(JournalEntryLine)
                        .where(
                            JournalEntryLine.journal_entry_id
                            == original_journal_id
                        )
                        .order_by(JournalEntryLine.line_no)
                    )
                ).scalars().all()

                assert len(lines) == 2

                by_account = {
                    line.account_id: line
                    for line in lines
                }

                payable_line = by_account[payroll_payable.id]
                bank_line = by_account[bank_gl.id]

                assert Decimal(payable_line.debit) == Decimal(
                    "7700.00"
                )
                assert Decimal(payable_line.credit) == Decimal(
                    "0.00"
                )
                assert Decimal(bank_line.debit) == Decimal("0.00")
                assert Decimal(bank_line.credit) == Decimal(
                    "7700.00"
                )

                total_debit = sum(
                    (Decimal(line.debit) for line in lines),
                    Decimal("0.00"),
                )
                total_credit = sum(
                    (Decimal(line.credit) for line in lines),
                    Decimal("0.00"),
                )

                assert total_debit == Decimal("7700.00")
                assert total_credit == Decimal("7700.00")
                assert total_debit == total_credit

                generic_payment_count = await db.scalar(
                    select(func.count(Payment.id)).where(
                        Payment.company_id == company.id
                    )
                )
                assert generic_payment_count == 0

                foreign_company = Company(
                    **_required_values(
                        Company,
                        {
                            "name": (
                                "Payroll Disbursement Foreign "
                                f"{uuid4().hex[:10]}"
                            ),
                        },
                    )
                )
                db.add(foreign_company)
                await db.flush()

                assert (
                    await get_payroll_disbursement(
                        db,
                        company_id=foreign_company.id,
                        payroll_calculation_id=calculation.id,
                    )
                    is None
                )

                with pytest.raises(
                    PayrollDisbursementNotFoundError
                ):
                    await create_payroll_disbursement(
                        db,
                        company_id=foreign_company.id,
                        payroll_calculation_id=calculation.id,
                        bank_account_id=bank.id,
                        payment_date=payment_date,
                        created_by=user.id,
                    )

                reversal = (
                    await reverse_payroll_disbursement_journal(
                        db,
                        company_id=company.id,
                        payroll_disbursement_id=disbursement.id,
                        reversal_date=payment_date,
                        reversed_by=user.id,
                    )
                )
                await db.flush()

                assert reversal.id != original_journal_id
                assert reversal.status == "posted"
                assert reversal.reversal_of_id == original_journal_id
                assert (
                    reversal.payroll_disbursement_id
                    == disbursement.id
                )
                assert reversal.payroll_calculation_id is None

                await db.refresh(journal)
                assert journal.status == "reversed"

                reversal_lines = (
                    await db.execute(
                        select(JournalEntryLine)
                        .where(
                            JournalEntryLine.journal_entry_id
                            == reversal.id
                        )
                        .order_by(JournalEntryLine.line_no)
                    )
                ).scalars().all()

                assert len(reversal_lines) == 2

                reversal_by_account = {
                    line.account_id: line
                    for line in reversal_lines
                }

                reversal_payable = reversal_by_account[
                    payroll_payable.id
                ]
                reversal_bank = reversal_by_account[bank_gl.id]

                assert Decimal(
                    reversal_payable.debit
                ) == Decimal("0.00")
                assert Decimal(
                    reversal_payable.credit
                ) == Decimal("7700.00")

                assert Decimal(
                    reversal_bank.debit
                ) == Decimal("7700.00")
                assert Decimal(
                    reversal_bank.credit
                ) == Decimal("0.00")

                reversal_debit = sum(
                    (
                        Decimal(line.debit)
                        for line in reversal_lines
                    ),
                    Decimal("0.00"),
                )
                reversal_credit = sum(
                    (
                        Decimal(line.credit)
                        for line in reversal_lines
                    ),
                    Decimal("0.00"),
                )

                assert reversal_debit == Decimal("7700.00")
                assert reversal_credit == Decimal("7700.00")
                assert reversal_debit == reversal_credit

                payment_count_after_reversal = await db.scalar(
                    select(func.count(Payment.id)).where(
                        Payment.company_id == company.id
                    )
                )
                assert payment_count_after_reversal == 0

                print("PAYROLL DISBURSEMENT SNAPSHOT = PASS")
                print("NET PAY 7700.00 = PASS")
                print("EMPLOYEE IBAN SNAPSHOT = PASS")
                print("DISBURSEMENT IDEMPOTENCY = PASS")
                print("GL DEBIT PAYROLL PAYABLE 7700.00 = PASS")
                print("GL CREDIT BANK 7700.00 = PASS")
                print("GL BALANCE = PASS")
                print("JOURNAL PROVENANCE = PASS")
                print("JOURNAL IDEMPOTENCY = PASS")
                print("COMPANY ISOLATION = PASS")
                print("GENERIC PAYMENT ROWS = 0 PASS")
                print("REVERSAL PROVENANCE = PASS")
                print("REVERSAL LINES = PASS")

                await db.rollback()

    finally:
        await engine.dispose()
