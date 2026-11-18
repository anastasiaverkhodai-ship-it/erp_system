"""Paid-income period provenance must not depend on ORM cache state.

Only the disposable PostgreSQL copy is allowed. Payroll calculation, posting,
funding, paid-income freeze and reads use real services without mocks.
"""
import os
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_insurance_funding import setup, receipt, request
from app.models.employee import Employee
from app.models.payroll import PayrollPeriod
from app.services.payroll_disbursement_service import (
    create_payroll_disbursement,
    generate_and_post_payroll_disbursement_journal,
)
from app.services.payroll_insurance_funding_service import register_insurance_funding
from app.services.payroll_paid_income_calculation import PaidIncomeError
from app.services.payroll_paid_income_service import read_paid_income

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E') != '1', reason='Isolated PostgreSQL required'),
]


async def test_changed_persisted_period_rejected_with_cached_period(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        database = await db.scalar(text('SELECT current_database()'))
        assert database.startswith('erp_r3_copy_'), 'Disposable PostgreSQL copy required'
        company, actor, contract, calculation, _, accounts, bank = await setup(db)
        funding_journal = await receipt(db, company, actor, accounts, bank, Decimal('400'))
        await register_insurance_funding(
            db, company_id=company.id,
            data=request(funding_journal, bank, calculation, Decimal('400'), 'live-period-funding'),
            created_by=actor.id,
        )
        employee = await db.get(Employee, contract.employee_id)
        employee.payment_iban = 'UA213223130000026007233566001'
        await db.flush()
        payment = await create_payroll_disbursement(
            db, company_id=company.id, payroll_calculation_id=calculation.id,
            bank_account_id=bank.id, payment_date=date(2026, 9, 30),
            created_by=actor.id, amount=Decimal('100'), request_key='live-period-payment',
        )
        await generate_and_post_payroll_disbursement_journal(
            db, company_id=company.id, payroll_disbursement_id=payment.id,
            created_by=actor.id,
        )
        args = dict(company_id=company.id, payroll_calculation_id=calculation.id)
        original = await read_paid_income(db, **args)
        assert original['status'] == 'COMPLETE'
        assert original['rows'][0]['accrual_month'] == '2026-09'
        period = await db.get(PayrollPeriod, calculation.payroll_period_id)
        assert period.start_date == date(2026, 9, 1)

        # Raw SQL deliberately leaves the already-loaded ORM period stale.
        # No immutable payment record or protected production object is edited.
        await db.execute(text('''
            UPDATE payroll_periods SET start_date = :changed
            WHERE company_id = :company AND id = :period
        '''), dict(changed=date(2026, 8, 1), company=company.id, period=period.id))
        assert await db.scalar(text('SELECT start_date FROM payroll_periods WHERE id=:id'),
                               {'id': period.id}) == date(2026, 8, 1)
        assert period.start_date == date(2026, 9, 1)

        # An uncached read already rejects the changed period. The cached read
        # must reject it as well; cache state cannot grant source provenance.
        cached_rejected = False
        try:
            await read_paid_income(db, **args)
        except PaidIncomeError:
            cached_rejected = True
        await db.refresh(period)
        with pytest.raises(PaidIncomeError, match='identity differs from live source'):
            await read_paid_income(db, **args)
        assert cached_rejected, 'Cached period concealed changed PostgreSQL accrual month'
        await db.rollback()


@pytest.mark.parametrize('changed, changed_end, rejected', [
    (date(2026, 8, 1), date(2026, 8, 31), True),
    (date(2026, 10, 1), date(2026, 10, 31), True),
    (date(2026, 9, 1), date(2026, 9, 30), False),
])
async def test_live_period_cached_and_refreshed_agree(employee_engine, changed, changed_end, rejected):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        database = await db.scalar(text('SELECT current_database()'))
        assert database.startswith('erp_r3_copy_'), 'Disposable PostgreSQL copy required'
        company, actor, contract, calculation, _, accounts, bank = await setup(db)
        funding_journal = await receipt(db, company, actor, accounts, bank, Decimal('400'))
        await register_insurance_funding(
            db, company_id=company.id,
            data=request(funding_journal, bank, calculation, Decimal('400'), 'live-period-funding'),
            created_by=actor.id,
        )
        employee = await db.get(Employee, contract.employee_id)
        employee.payment_iban = 'UA213223130000026007233566001'
        await db.flush()
        payment = await create_payroll_disbursement(
            db, company_id=company.id, payroll_calculation_id=calculation.id,
            bank_account_id=bank.id, payment_date=date(2026, 9, 30),
            created_by=actor.id, amount=Decimal('100'), request_key='live-period-payment',
        )
        await generate_and_post_payroll_disbursement_journal(
            db, company_id=company.id, payroll_disbursement_id=payment.id,
            created_by=actor.id,
        )
        args = dict(company_id=company.id, payroll_calculation_id=calculation.id)
        original = await read_paid_income(db, **args)
        assert original['status'] == 'COMPLETE'
        assert original['rows'][0]['accrual_month'] == '2026-09'
        period = await db.get(PayrollPeriod, calculation.payroll_period_id)
        assert period.start_date == date(2026, 9, 1)


        await db.execute(text(
            'UPDATE payroll_periods SET start_date=:changed, end_date=:changed_end WHERE id=:id'
        ), {'changed': changed, 'changed_end': changed_end, 'id': period.id})
        assert period.start_date == date(2026, 9, 1)
        for refresh in (False, True):
            if refresh:
                await db.refresh(period)
            if rejected:
                with pytest.raises(PaidIncomeError, match='identity differs from live source'):
                    await read_paid_income(db, **args)
            else:
                current = await read_paid_income(db, **args)
                assert current == original
                assert current['official_form_approved'] is False
        await db.rollback()
