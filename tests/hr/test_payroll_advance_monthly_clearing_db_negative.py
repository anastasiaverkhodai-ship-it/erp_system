from test_employee_foundation import employee_engine
"""Real PostgreSQL negative integration and zero-leak coverage."""

from datetime import date
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
import pytest

from app.models.journal_entry import JournalEntry
from app.models.payroll_advance import PayrollAdvance
from app.models.payroll_tax_remittance import PayrollTaxRemittance
from app.services.payroll_advance_monthly_tax_clearing import (
    MonthlyTaxClearingError,
)
from app.services.payroll_advance_monthly_tax_clearing_db import (
    reconcile_monthly_advance_taxes_from_db,
)
from test_payroll_insurance_funding import setup


@pytest.mark.asyncio
async def test_clearing_rejects_unmatched_sources_without_db_leaks(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, user, contract, calc, accrual, accounts, bank = (
            await setup(db)
        )

        async def counts():
            result = {}
            for model in (
                JournalEntry,
                PayrollAdvance,
                PayrollTaxRemittance,
            ):
                result[model.__tablename__] = await db.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.company_id == company.id)
                )
            return result

        before = await counts()

        # Caller must never be allowed to use another company.
        with pytest.raises(MonthlyTaxClearingError):
            await reconcile_monthly_advance_taxes_from_db(
                db,
                company_id=company.id + 999999,
                payroll_advance_id=999999999,
                payroll_calculation_id=calc.id,
                as_of=date(2026, 9, 30),
            )

        # A real monthly calculation is insufficient without
        # an advance from the same company, period and contract.
        with pytest.raises(
            MonthlyTaxClearingError,
            match="Company-scoped payroll sources not found",
        ):
            await reconcile_monthly_advance_taxes_from_db(
                db,
                company_id=company.id,
                payroll_advance_id=999999999,
                payroll_calculation_id=calc.id,
                as_of=date(2026, 9, 30),
            )

        # Invalid identities and as-of dates must fail closed.
        for kwargs in (
            dict(company_id=0),
            dict(payroll_advance_id=-1),
            dict(payroll_calculation_id=0),
            dict(as_of="2026-09-30"),
        ):
            arguments = dict(
                company_id=company.id,
                payroll_advance_id=999999999,
                payroll_calculation_id=calc.id,
                as_of=date(2026, 9, 30),
            )
            arguments.update(kwargs)
            with pytest.raises(MonthlyTaxClearingError):
                await reconcile_monthly_advance_taxes_from_db(
                    db, **arguments
                )

        after = await counts()
        assert before == after, (before, after)

        print("REAL MONTHLY CALCULATION SOURCE = PASS")
        print("MISSING ADVANCE FAIL-CLOSED = PASS")
        print("WRONG COMPANY FAIL-CLOSED = PASS")
        print("INVALID INPUT GUARDS = PASS")
        print("FINANCIAL ROW COUNT ZERO-LEAK = PASS")

        # Deliberately discard the entire test transaction.
        await db.rollback()
        print("TEST TRANSACTION ROLLBACK = PASS")
