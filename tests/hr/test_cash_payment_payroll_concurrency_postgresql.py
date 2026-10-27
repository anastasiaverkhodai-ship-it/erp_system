"""Payment and PayrollDisbursement compete for one real cash balance.

Executed only in the isolated PostgreSQL copy.
The losing financial transaction must leave no journal.
"""

import asyncio
import os
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

from app.core.config import settings
from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.cash_desk import CashDesk
from app.models.company import Company
from app.models.counterparty import Counterparty
from app.models.employee import Employee
from app.models.employee_salary_rate import EmployeeSalaryRate
from app.models.employment_contract import EmploymentContract
from app.models.journal_entry import JournalEntry
from app.models.payment import Payment
from app.models.payroll import (
    PayrollInput,
    PayrollInputSalarySlice,
    PayrollPeriod,
    PayrollPeriodStatus,
)
from app.models.payroll_disbursement import (
    CashDeskOpeningEvidence,
    PayrollDisbursement,
)
from app.models.payroll_statutory import PayrollStatutoryComponent
from app.models.user import User

from app.services.account_types import (
    AccountNormalBalance,
    AccountType,
)
from app.services.counterparty_types import CounterpartyType
from app.services.cash_desk_opening_review_service import (
    record_independent_cash_opening_review,
)
from app.services.cash_physical_balance_guard import (
    validate_locked_physical_cash,
)
from app.services.cash_physical_posting_enforcement import (
    CashPostingEnforcementError,
)
from app.services.payment_lifecycle_service import (
    confirm_payment,
    create_payment_draft,
)
from app.services.payment_types import PaymentDirection, PaymentStatus
from app.services.payroll_accounting_service import (
    generate_and_post_payroll_journal_entry,
)
from app.services.payroll_calculation_service import (
    calculate_payroll_input,
)
from app.services.payroll_disbursement_service import (
    create_payroll_disbursement,
    generate_and_post_payroll_disbursement_journal,
)
from app.services.payroll_statutory_service import (
    calculate_payroll_statutory_result,
    create_statutory_rate,
)


def postgres_url():
    url = settings.database_url
    assert "postgresql" in url
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    if url.startswith("postgresql+psycopg://"):
        return "postgresql+asyncpg://" + url[
            len("postgresql+psycopg://"):
        ]
    return url


def required_values(model, overrides):
    """Match the existing payroll PostgreSQL fixture contract."""
    values = dict(overrides)
    for col in model.__table__.columns:
        if col.name in values:
            continue
        if col.primary_key and col.autoincrement:
            continue
        if col.nullable or col.default is not None:
            continue
        if col.server_default is not None:
            continue
        if col.name.lower().endswith("_id"):
            continue

        try:
            kind = col.type.python_type
        except Exception:
            kind = None

        if kind is str:
            values[col.name] = "e2e-" + col.name
        elif kind is int:
            values[col.name] = 1
        elif kind is bool:
            values[col.name] = True
        elif kind is date:
            values[col.name] = date(2026, 1, 1)
        elif kind is Decimal:
            values[col.name] = Decimal("1.00")

    return values


@pytest.mark.asyncio
async def test_payment_blocks_concurrent_payroll_overdraft():
    assert os.getenv("RUN_POSTGRES_E2E") == "1"

    engine = create_async_engine(
        postgres_url(),
        pool_pre_ping=True,
    )
    Session = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    token = uuid4().hex[:10]
    payment_date = date(2026, 9, 30)
    opening_date = date(2026, 9, 1)

    try:
        # All fixture rows are committed ONLY in the disposable DB.
        async with Session() as setup:
            async with setup.begin():
                company = Company(**required_values(
                    Company,
                    {"name": "Cross Source Cash " + token},
                ))
                setup.add(company)
                await setup.flush()

                actor = User(**required_values(User, {
                    "email": f"cash-cross-{token}@example.invalid",
                    "password_hash": "not-used",
                    "first_name": "Cash",
                    "last_name": "Actor",
                    "is_active": True,
                }))
                reviewer = User(**required_values(User, {
                    "email": f"cash-review-{token}@example.invalid",
                    "password_hash": "not-used",
                    "first_name": "Cash",
                    "last_name": "Reviewer",
                    "is_active": True,
                }))
                setup.add_all([actor, reviewer])
                await setup.flush()
                assert actor.id != reviewer.id

                employee = Employee(**required_values(Employee, {
                    "company_id": company.id,
                    "employee_number": "CROSS-" + token,
                    "first_name": "Payroll",
                    "last_name": "Employee",
                    "payment_iban": "UA123456789012345678901234567",
                    "hire_date": date(2026, 1, 1),
                    "created_by": actor.id,
                    "status": "active",
                }))
                setup.add(employee)
                await setup.flush()

                contract = EmploymentContract(**required_values(
                    EmploymentContract,
                    {
                        "company_id": company.id,
                        "employee_id": employee.id,
                        "contract_number": "CROSS-C-" + token,
                        "contract_type": "standard",
                        "work_arrangement": "full_time",
                        "start_date": date(2026, 1, 1),
                        "end_date": None,
                        "status": "active",
                        "created_by": actor.id,
                    },
                ))
                setup.add(contract)

                counterparty = Counterparty(
                    company_id=company.id,
                    name="Cash Supplier " + token,
                    counterparty_type=CounterpartyType.BOTH,
                    default_currency_code="UAH",
                    is_active=True,
                )
                setup.add(counterparty)

                period = PayrollPeriod(
                    company_id=company.id,
                    year=2026,
                    month=9,
                    start_date=opening_date,
                    end_date=payment_date,
                    status=PayrollPeriodStatus.FINALIZED,
                    created_by=actor.id,
                    finalized_by=actor.id,
                    finalized_at=datetime(
                        2026, 10, 1, 12, 0, tzinfo=UTC,
                    ),
                )
                setup.add(period)
                setup.add(AccountingPeriod(
                    company_id=company.id,
                    year=2026,
                    month=9,
                    start_date=opening_date,
                    end_date=payment_date,
                    status="open",
                    is_locked=False,
                ))
                await setup.flush()

                accounts = []
                for code, kind, normal, system in (
                    ("66", AccountType.LIABILITY,
                     AccountNormalBalance.CREDIT, True),
                    ("681", AccountType.LIABILITY,
                     AccountNormalBalance.CREDIT, True),
                    ("371", AccountType.ASSET,
                     AccountNormalBalance.DEBIT, True),
                    ("301", AccountType.ASSET,
                     AccountNormalBalance.DEBIT, False),
                    ("92", AccountType.EXPENSE,
                     AccountNormalBalance.DEBIT, True),
                    ("64", AccountType.LIABILITY,
                     AccountNormalBalance.CREDIT, True),
                    ("65", AccountType.LIABILITY,
                     AccountNormalBalance.CREDIT, True),
                ):
                    accounts.append(Account(
                        company_id=company.id,
                        code=code,
                        name="Cross Source " + code,
                        account_type=kind,
                        normal_balance=normal,
                        is_postable=True,
                        is_system=system,
                        is_active=True,
                    ))

                setup.add_all(accounts)
                await setup.flush()

                cash_gl = next(
                    a for a in accounts if a.code == "301"
                )
                desk = CashDesk(
                    company_id=company.id,
                    name="Shared Physical Cash " + token,
                    code="CS-" + token,
                    currency_code="UAH",
                    accounting_account_id=cash_gl.id,
                    is_active=True,
                )
                setup.add(desk)
                await setup.flush()

                payroll_input = PayrollInput(
                    company_id=company.id,
                    payroll_period_id=period.id,
                    employment_contract_id=contract.id,
                    scheduled_minutes=6000,
                    worked_minutes=6000,
                    leave_days=0,
                    sick_days=0,
                    manual_adjustment_amount=Decimal("0.00"),
                    created_by=actor.id,
                )
                setup.add(payroll_input)
                await setup.flush()

                rate = EmployeeSalaryRate(
                    company_id=company.id,
                    employment_contract_id=contract.id,
                    rate_type="hourly",
                    amount=Decimal("100.00"),
                    currency_code="UAH",
                    effective_from=opening_date,
                    effective_to=payment_date,
                    created_by=actor.id,
                )
                setup.add(rate)
                await setup.flush()

                setup.add(PayrollInputSalarySlice(
                    company_id=company.id,
                    payroll_input_id=payroll_input.id,
                    salary_rate_id=rate.id,
                    salary_rate_type="hourly",
                    salary_rate_amount=Decimal("100.00"),
                    currency_code="UAH",
                    effective_from=opening_date,
                    effective_to=payment_date,
                    scheduled_minutes=6000,
                    worked_minutes=6000,
                ))
                await setup.flush()

                calculation = await calculate_payroll_input(
                    setup,
                    company_id=company.id,
                    payroll_input_id=payroll_input.id,
                    calculated_by=actor.id,
                )
                assert Decimal(calculation.gross_amount) == (
                    Decimal("10000.00")
                )

                for component, tax_rate in (
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
                        setup,
                        company_id=company.id,
                        component=component,
                        rate=tax_rate,
                        effective_from=date(2026, 1, 1),
                        effective_to=None,
                        actor_user_id=actor.id,
                    )

                statutory = await calculate_payroll_statutory_result(
                    setup,
                    company_id=company.id,
                    payroll_calculation_id=calculation.id,
                    actor_user_id=actor.id,
                )
                assert Decimal(statutory.net_amount) == (
                    Decimal("7700.00")
                )

                await generate_and_post_payroll_journal_entry(
                    setup,
                    company_id=company.id,
                    payroll_calculation_id=calculation.id,
                    created_by=actor.id,
                )

                payout = await create_payroll_disbursement(
                    setup,
                    company_id=company.id,
                    payroll_calculation_id=calculation.id,
                    bank_account_id=None,
                    cash_desk_id=desk.id,
                    payment_date=payment_date,
                    created_by=actor.id,
                    amount=Decimal("70.00"),
                    request_key="CROSS-PAYROLL-" + token,
                )

                payment = await create_payment_draft(
                    setup,
                    company_id=company.id,
                    counterparty_id=counterparty.id,
                    contract_id=None,
                    number="CROSS-PAYMENT-" + token,
                    direction=PaymentDirection.OUTGOING,
                    payment_date=payment_date,
                    currency_code="UAH",
                    amount=Decimal("70.00"),
                    created_by=actor.id,
                    cash_desk_id=desk.id,
                )

                opening = CashDeskOpeningEvidence(
                    company_id=company.id,
                    cash_desk_id=desk.id,
                    effective_date=opening_date,
                    declared_amount=Decimal("100.00"),
                    currency_code="UAH",
                    evidence_reference="CROSS-COUNT-" + token,
                    attested_by=actor.id,
                )
                setup.add(opening)
                await setup.flush()

                review = await record_independent_cash_opening_review(
                    setup,
                    company_id=company.id,
                    opening_evidence_id=opening.id,
                    reviewed_by=reviewer.id,
                    review_reference="CROSS-REVIEW-" + token,
                )
                assert review.id is not None

                company_id = company.id
                actor_id = actor.id
                desk_id = desk.id
                payment_id = payment.id
                payout_id = payout.id

        print("CROSS-SOURCE REVIEWED OPENING = PASS", flush=True)

        async def payroll_writer():
            async with Session() as session:
                async with session.begin():
                    return await generate_and_post_payroll_disbursement_journal(
                        session,
                        company_id=company_id,
                        payroll_disbursement_id=payout_id,
                        created_by=actor_id,
                    )

        # Payment takes the CashDesk lock first and keeps its
        # transaction uncommitted. Payroll must wait for that lock.
        async with Session() as first_session:
            async with first_session.begin():
                await confirm_payment(
                    first_session,
                    company_id=company_id,
                    payment_id=payment_id,
                    confirmed_by=actor_id,
                )
                await first_session.flush()

                second_task = asyncio.create_task(payroll_writer())

                try:
                    await asyncio.sleep(0.35)
                    assert not second_task.done(), (
                        "STOP: Payroll did not wait for "
                        "the first cash transaction"
                    )
                    print(
                        "PAYROLL WAITS FOR PAYMENT CASH LOCK = PASS",
                        flush=True,
                    )
                except BaseException:
                    second_task.cancel()
                    await asyncio.gather(
                        second_task,
                        return_exceptions=True,
                    )
                    raise

        print("FIRST PAYMENT CASH COMMIT = PASS", flush=True)

        with pytest.raises(CashPostingEnforcementError):
            await asyncio.wait_for(
                second_task,
                timeout=30,
            )

        print(
            "PAYMENT VS PAYROLL OVERDRAFT = REJECTED",
            flush=True,
        )

        async with Session() as verify:
            payment_row = await verify.get(Payment, payment_id)
            assert payment_row.status == PaymentStatus.CONFIRMED

            payroll_journals = await verify.scalar(
                select(func.count(JournalEntry.id)).where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.payroll_disbursement_id == payout_id,
                )
            )
            assert payroll_journals == 0

            payment_journals = await verify.scalar(
                select(func.count(JournalEntry.id)).where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.payment_id == payment_id,
                )
            )
            assert payment_journals == 1

            balance = await validate_locked_physical_cash(
                verify,
                company_id=company_id,
                cash_desk_id=desk_id,
                as_of_date=payment_date,
            )
            assert balance.closing_balance == Decimal("30.00")

        print("FAILED PAYROLL GL LEAK = 0", flush=True)
        print("SUCCESSFUL PAYMENT JOURNAL COUNT = 1", flush=True)
        print("CROSS-SOURCE CASH BALANCE = 30.00", flush=True)
        print("REAL PAYMENT VS PAYROLL CONCURRENCY = PASS", flush=True)

    finally:
        await engine.dispose()
