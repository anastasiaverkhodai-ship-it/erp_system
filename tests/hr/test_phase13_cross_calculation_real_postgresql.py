"""Real payroll cross-calculation financial PostgreSQL acceptance.

No mocked payroll creation, statutory calculation, journal posting,
or paid-income snapshot. Runs only in a disposable PostgreSQL copy.
"""
import calendar

import importlib.util
import os
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models.payroll_disbursement import PayrollDisbursement
from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.models.payroll_paid_income import PayrollPaidIncome

from app.services.payroll_disbursement_service import PayrollDisbursementSourceStateError

from app.services.payroll_cross_calculation_posting import (
    post_cross_calculation_payments,
)

from app.services.payroll_paid_income_service import (
    read_paid_income,
)

pytestmark = pytest.mark.asyncio


def _fixture_module():
    path = (
        Path(__file__).resolve().parent
        / "test_payroll_disbursement_postgresql.py"
    )
    spec = importlib.util.spec_from_file_location(
        "phase13_crosscalc_existing_fixture",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module._seed_identity)
    return module


def _postgres_url():
    raw = os.environ.get("DATABASE_URL")
    assert raw, "Isolated DATABASE_URL is required"

    url = make_url(raw)

    assert url.username == "erp_user"
    assert url.host in ("127.0.0.1", "localhost")
    assert re.fullmatch(
        r"erp_r3_copy_[0-9a-f]{16}",
        url.database or "",
    ), "Refusing non-isolated PostgreSQL"

    return raw


async def _create_real_sources(db, base):
    company, user, employee, contract = (
        await base._seed_identity(db)
    )

    payable = base.Account(
        company_id=company.id,
        code="66",
        name="Payroll payable",
        account_type=base.AccountType.LIABILITY,
        normal_balance=base.AccountNormalBalance.CREDIT,
        is_postable=True,
        is_system=True,
        is_active=True,
    )
    bank_gl = base.Account(
        company_id=company.id,
        code="311",
        name="Payroll bank",
        account_type=base.AccountType.ASSET,
        normal_balance=base.AccountNormalBalance.DEBIT,
        is_postable=True,
        is_system=False,
        is_active=True,
    )
    db.add_all([payable, bank_gl])
    await db.flush()

    bank = base.BankAccount(
        company_id=company.id,
        name="Crosscalc E2E bank",
        account_number="UA-CROSSCALC-E2E",
        currency_code="UAH",
        accounting_account_id=bank_gl.id,
        is_active=True,
    )
    db.add(bank)

    for code, kind, normal in (
        ("92", base.AccountType.EXPENSE,
         base.AccountNormalBalance.DEBIT),
        ("64", base.AccountType.LIABILITY,
         base.AccountNormalBalance.CREDIT),
        ("65", base.AccountType.LIABILITY,
         base.AccountNormalBalance.CREDIT),
    ):
        db.add(base.Account(
            company_id=company.id,
            code=code,
            name=code,
            account_type=kind,
            normal_balance=normal,
            is_postable=True,
            is_system=True,
            is_active=True,
        ))

    for month in (9, 10, 11):
        start = date(2026, month, 1)
        end = date(2026, month, calendar.monthrange(2026, month)[1])
        db.add(base.AccountingPeriod(
            company_id=company.id,
            year=2026,
            month=month,
            start_date=start,
            end_date=end,
            status="open",
            is_locked=False,
        ))

    await db.flush()

    for component, rate in (
        (base.PayrollStatutoryComponent.PERSONAL_INCOME_TAX,
         Decimal("0.18")),
        (base.PayrollStatutoryComponent.MILITARY_LEVY,
         Decimal("0.05")),
        (base.PayrollStatutoryComponent.UNIFIED_SOCIAL_CONTRIBUTION,
         Decimal("0.22")),
    ):
        await base.create_statutory_rate(
            db,
            company_id=company.id,
            component=component,
            rate=rate,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            actor_user_id=user.id,
        )

    calculations = []

    for month in (9, 10):
        start = date(2026, month, 1)
        end = date(2026, month, calendar.monthrange(2026, month)[1])

        period = base.PayrollPeriod(
            company_id=company.id,
            year=2026,
            month=month,
            start_date=start,
            end_date=end,
            status=base.PayrollPeriodStatus.FINALIZED,
            created_by=user.id,
            finalized_by=user.id,
            finalized_at=datetime(
                2026, 11, 1, 12, 0, tzinfo=UTC
            ),
        )
        db.add(period)
        await db.flush()

        payroll_input = base.PayrollInput(
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

        rate = base.EmployeeSalaryRate(
            company_id=company.id,
            employment_contract_id=contract.id,
            rate_type="hourly",
            amount=Decimal("100.00"),
            currency_code="UAH",
            effective_from=start,
            effective_to=end,
            created_by=user.id,
        )
        db.add(rate)
        await db.flush()

        db.add(base.PayrollInputSalarySlice(
            company_id=company.id,
            payroll_input_id=payroll_input.id,
            salary_rate_id=rate.id,
            salary_rate_type="hourly",
            salary_rate_amount=Decimal("100.00"),
            currency_code="UAH",
            effective_from=start,
            effective_to=end,
            scheduled_minutes=6000,
            worked_minutes=6000,
        ))
        await db.flush()

        calculation = await base.calculate_payroll_input(
            db,
            company_id=company.id,
            payroll_input_id=payroll_input.id,
            calculated_by=user.id,
        )
        await db.flush()

        assert Decimal(calculation.gross_amount) == Decimal(
            "10000.00"
        )

        statutory = (
            await base.calculate_payroll_statutory_result(
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

        accrual = (
            await base.generate_and_post_payroll_journal_entry(
                db,
                company_id=company.id,
                payroll_calculation_id=calculation.id,
                created_by=user.id,
            )
        )
        assert accrual.status == "posted"

        calculations.append(calculation)

    return company, user, employee, bank, calculations


async def _counts(db, company_id):
    return (
        await db.scalar(
            select(func.count(PayrollDisbursement.id)).where(
                PayrollDisbursement.company_id == company_id,
            )
        ),
        await db.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == company_id,
                JournalEntry.payroll_disbursement_id.is_not(None),
                JournalEntry.reversal_of_id.is_(None),
            )
        ),
        await db.scalar(
            select(func.count(PayrollPaidIncome.id)).where(
                PayrollPaidIncome.company_id == company_id,
            )
        ),
    )


async def test_real_cross_calculation_posting_and_rollback():
    base = _fixture_module()
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
                    bank,
                    calculations,
                ) = await _create_real_sources(db, base)

                ids = [x.id for x in calculations]
                assert len(set(ids)) == 2

                payment_date = date(2026, 11, 10)

                def item(calc, amount):
                    return {
                        "payroll_calculation_id": calc,
                        "amount": amount,
                    }

                async def run(key, second_amount):
                    return await post_cross_calculation_payments(
                        db,
                        company_id=company.id,
                        employee_id=employee.id,
                        bank_account_id=bank.id,
                        cash_desk_id=None,
                        payment_date=payment_date,
                        created_by=user.id,
                        request_key=key,
                        items=[
                            item(ids[0], "1000.00"),
                            item(ids[1], second_amount),
                        ],
                    )

                before = await _counts(db, company.id)

                # The first real payroll posting must roll back
                # if the second exceeds its statutory payable.
                with pytest.raises(PayrollDisbursementSourceStateError):
                    await run(
                        "real-rollback",
                        "999999.00",
                    )

                assert await _counts(db, company.id) == before

                result = await run(
                    "real-success",
                    "2000.00",
                )

                assert result["amount"] == "3000.00"
                assert len(result["components"]) == 2
                assert result["consolidated_bank_transfer"] is False

                disbursement_ids = [
                    x["payroll_disbursement_id"]
                    for x in result["components"]
                ]

                journal_ids = [
                    x["journal_entry_id"]
                    for x in result["components"]
                ]

                assert len(set(disbursement_ids)) == 2
                assert len(set(journal_ids)) == 2

                after = await _counts(db, company.id)

                assert after == (
                    before[0] + 2,
                    before[1] + 2,
                    before[2] + 2,
                )

                for component in result["components"]:
                    journal = await db.get(
                        JournalEntry,
                        component["journal_entry_id"],
                    )
                    assert journal is not None
                    assert journal.status == "posted"
                    assert (
                        journal.payroll_disbursement_id
                        == component["payroll_disbursement_id"]
                    )

                    lines = (
                        await db.scalars(
                            select(JournalEntryLine).where(
                                JournalEntryLine.journal_entry_id
                                == journal.id
                            )
                        )
                    ).all()

                    debit = sum(
                        (Decimal(x.debit) for x in lines),
                        Decimal("0.00"),
                    )
                    credit = sum(
                        (Decimal(x.credit) for x in lines),
                        Decimal("0.00"),
                    )

                    assert debit == credit
                    assert debit > 0

                    snapshot = await db.scalar(
                        select(PayrollPaidIncome).where(
                            PayrollPaidIncome.company_id
                            == company.id,
                            PayrollPaidIncome.payroll_disbursement_id
                            == component["payroll_disbursement_id"],
                        )
                    )
                    assert snapshot is not None

                # Exact replay: same disbursements and journals.
                repeated = await run(
                    "real-success",
                    "2000.00",
                )
                assert repeated["components"] == result["components"]
                assert await _counts(db, company.id) == after

                # A changed request cannot silently reuse the key.
                with pytest.raises(PayrollDisbursementSourceStateError):
                    await run(
                        "real-success",
                        "2001.00",
                    )
                assert await _counts(db, company.id) == after

                # Real bank source: one outgoing statement line
                # corresponds to two previously posted payroll payouts.
                from app.services.payroll_bank_batch_validation import (
                    validate_payroll_bank_batch,
                    PayrollBankBatchValidationError,
                )

                statement = base.BankStatement(
                    **base._required_values(
                        base.BankStatement,
                        {
                            "company_id": company.id,
                            "bank_account_id": bank.id,
                            "external_id": "CROSSCALC-BATCH-REAL-E2E",
                            "created_by": user.id,
                        },
                    )
                )
                db.add(statement)
                await db.flush()

                bank_line = base.BankStatementLine(
                    **base._required_values(
                        base.BankStatementLine,
                        {
                            "company_id": company.id,
                            "bank_account_id": bank.id,
                            "bank_statement_id": statement.id,
                            "external_line_id": "CROSSCALC-BATCH-LINE",
                            "amount": Decimal("-3000.00"),
                            "currency_code": "UAH",
                        },
                    )
                )
                db.add(bank_line)
                await db.flush()

                bank_review = await validate_payroll_bank_batch(
                    db,
                    company_id=company.id,
                    employee_id=employee.id,
                    bank_statement_line_id=bank_line.id,
                    payroll_disbursement_ids=disbursement_ids,
                )
                assert bank_review["amount"] == "3000.00"
                assert bank_review["member_count"] == 2
                assert bank_review["new_journals_created"] == 0
                assert bank_review["reconciliation_posted"] is False
                assert await _counts(db, company.id) == after

                with pytest.raises(
                    PayrollBankBatchValidationError,
                    match="duplicate",
                ):
                    await validate_payroll_bank_batch(
                        db,
                        company_id=company.id,
                        employee_id=employee.id,
                        bank_statement_line_id=bank_line.id,
                        payroll_disbursement_ids=[
                            disbursement_ids[0],
                            disbursement_ids[0],
                        ],
                    )

                with pytest.raises(
                    PayrollBankBatchValidationError,
                    match="does not equal",
                ):
                    async with db.begin_nested() as mismatch_savepoint:
                        bank_line.amount = Decimal("-3001.00")
                        await db.flush()
                        try:
                            await validate_payroll_bank_batch(
                                db,
                                company_id=company.id,
                                employee_id=employee.id,
                                bank_statement_line_id=bank_line.id,
                                payroll_disbursement_ids=disbursement_ids,
                            )
                        finally:
                            await mismatch_savepoint.rollback()
                            db.expire(bank_line)

                assert await _counts(db, company.id) == after
                print("REAL BANK BATCH SOURCE VALIDATION = PASS")
                print("BANK LINE TO TWO PAYOUTS = VERIFIED")
                print("RECONCILIATION JOURNAL DUPLICATES = 0")

                # Verify that the existing paid-income reader
                # can still resolve both payroll calculations.
                for calculation_id in ids:
                    review = await read_paid_income(
                        db,
                        company_id=company.id,
                        payroll_calculation_id=calculation_id,
                    )
                    assert review["payroll_calculation_id"] == (
                        calculation_id
                    )
                    assert review["official_form_approved"] is False

                print("REAL CROSS-CALC PAYROLL = PASS")
                print("TWO REAL DISBURSEMENTS = PASS")
                print("TWO POSTED BALANCED JOURNALS = PASS")
                print("PAID-INCOME PROVENANCE = PASS")
                print("SECOND SOURCE FAILURE ROLLBACK = PASS")
                print("EXACT REQUEST REPLAY = PASS")
                print("CHANGED REQUEST REJECTED = PASS")

                # Do not persist E2E fixture rows.
                await db.rollback()

    finally:
        await engine.dispose()
