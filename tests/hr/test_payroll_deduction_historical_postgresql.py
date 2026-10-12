import os
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll import (
    PayrollPeriod,
    PayrollInput,
    PayrollCalculation,
)
from app.models.payroll_statutory import PayrollStatutoryResult
from app.models.payroll_deduction_result import PayrollDeductionResult
from app.models.journal_entry import JournalEntry

from app.services.payroll_deduction_accounting_service import (
    generate_and_post_payroll_deduction_journal,
)
from app.services.payroll_deduction_result_service import (
    PayrollDeductionResultConflictError,
)

from test_employee_foundation import employee_engine
from test_payroll_deduction_postgresql import _seed_identity


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_E2E") != "1",
        reason="Real isolated PostgreSQL required",
    ),
]



async def test_historical_unauthorized_deduction_reversal(employee_engine):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
        autoflush=False,
    ) as db:
        try:
            company, actor, _, contract = await _seed_identity(db)

            period = PayrollPeriod(
                company_id=company.id,
                year=2026,
                month=9,
                start_date=date(2026, 9, 1),
                end_date=date(2026, 9, 30),
                created_by=actor.id,
            )
            db.add(period)
            await db.flush()

            payroll_input = PayrollInput(
                company_id=company.id,
                payroll_period_id=period.id,
                employment_contract_id=contract.id,
                scheduled_minutes=6000,
                worked_minutes=6000,
                created_by=actor.id,
            )
            db.add(payroll_input)
            await db.flush()

            calculation = PayrollCalculation(
                company_id=company.id,
                payroll_period_id=period.id,
                payroll_input_id=payroll_input.id,
                employment_contract_id=contract.id,
                currency_code="UAH",
                gross_amount=Decimal("10000.00"),
                calculated_by=actor.id,
                calculated_at=datetime.now(timezone.utc),
            )
            db.add(calculation)
            await db.flush()

            statutory = PayrollStatutoryResult(
                company_id=company.id,
                payroll_calculation_id=calculation.id,
                currency_code="UAH",
                gross_amount=Decimal("10000.00"),
                employee_withholding_amount=Decimal("2300.00"),
                employer_contribution_amount=Decimal("2200.00"),
                net_amount=Decimal("7700.00"),
                calculated_by=actor.id,
                calculated_at=datetime.now(timezone.utc),
            )
            db.add(statutory)
            await db.flush()

            deduction = PayrollDeductionResult(
                company_id=company.id,
                payroll_calculation_id=calculation.id,
                payroll_statutory_result_id=statutory.id,
                employment_contract_id=contract.id,
                statutory_net_amount=Decimal("7700.00"),
                deduction_amount=Decimal("654.00"),
                final_payable_amount=Decimal("7046.00"),
                currency_code="UAH",
                calculated_by=actor.id,
                calculated_at=datetime.now(timezone.utc),
            )
            db.add(deduction)
            await db.flush()


            from app.models.account import (
                Account,
                AccountType,
                AccountNormalBalance,
            )
            from app.models.journal_entry_line import JournalEntryLine
            from app.models.journal_entry import JournalEntryStatus
            from app.services.accounting_posting import (
                post_journal_entry,
            )
            from app.services.payroll_employee_balance_service import (
                employee_payroll_balance,
            )
            from app.services.payroll_deduction_accounting_service import (
                reverse_payroll_deduction_journal,
            )

            # Historical E2E accounting periods.
            # September: original journal.
            # October: reversal journal.
            from app.models.accounting_period import AccountingPeriod

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
            await db.flush()

            payable = Account(
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
                name="Deduction payable",
                account_type=AccountType.LIABILITY,
                normal_balance=AccountNormalBalance.CREDIT,
                is_postable=True,
                is_system=True,
                is_active=True,
            )

            db.add_all([payable, deduction_payable])
            await db.flush()

            # Historical data fixture: this deliberately bypasses
            # the current deduction creation service.
            historical = JournalEntry(
                company_id=company.id,
                payroll_deduction_result_id=deduction.id,
                entry_date=date(2026, 9, 30),
                description="Historical unauthorized deduction",
                status=JournalEntryStatus.DRAFT,
                created_by=actor.id,
            )

            historical.lines = [
                JournalEntryLine(
                    line_no=1,
                    account_id=payable.id,
                    debit=Decimal("654.00"),
                    credit=Decimal("0.00"),
                ),
                JournalEntryLine(
                    line_no=2,
                    account_id=deduction_payable.id,
                    debit=Decimal("0.00"),
                    credit=Decimal("654.00"),
                ),
            ]

            db.add(historical)
            await db.flush()

            await post_journal_entry(
                db,
                company.id,
                historical.id,
            )

            before = await employee_payroll_balance(
                db,
                company_id=company.id,
                employee_id=contract.employee_id,
                as_of=date(2026, 9, 30),
            )

            assert not before["complete"]
            assert (
                "unauthorized_payroll_deduction"
                in before["issues"]
            )

            assert all(
                row["balance"] is None
                for row in before["balances"]
            )

            reversal = await reverse_payroll_deduction_journal(
                db,
                company_id=company.id,
                payroll_deduction_result_id=deduction.id,
                reversal_date=date(2026, 10, 1),
                reversed_by=actor.id,
            )

            assert reversal.payroll_deduction_result_id == deduction.id
            assert reversal.reversal_of_id == historical.id

            after = await employee_payroll_balance(
                db,
                company_id=company.id,
                employee_id=contract.employee_id,
                as_of=date(2026, 10, 1),
            )


            # Reversal dated October 1 must not rewrite
            # the historical September 30 balance.
            historical_as_of_after_reversal = (
                await employee_payroll_balance(
                    db,
                    company_id=company.id,
                    employee_id=contract.employee_id,
                    as_of=date(2026, 9, 30),
                )
            )

            assert historical_as_of_after_reversal["complete"] is False

            assert (
                "unauthorized_payroll_deduction"
                in historical_as_of_after_reversal["issues"]
            )

            assert all(
                row["balance"] is None
                for row in historical_as_of_after_reversal["balances"]
            )

            print("BALANCE BEFORE =", before)
            print("BALANCE AFTER =", after)

            # A fully reversed historical deduction must remain
            # auditable but must not block the current balance.
            assert after["complete"] is True
            assert (
                "unauthorized_payroll_deduction"
                not in after["issues"]
            )

            assert all(
                row["deducted_amount"] == Decimal("0")
                for row in after["balances"]
            )

            assert all(
                row["balance"] is not None
                for row in after["balances"]
            )

        finally:
            await db.rollback()
