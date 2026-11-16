import os
from datetime import date
from decimal import Decimal as D
import pytest
from sqlalchemy import select, func, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_insurance_funding import setup, receipt, request
from app.models.employee import Employee
from app.models.payroll_paid_income import PayrollPaidIncome
from app.models.payroll_statutory import PayrollStatutoryResult
from app.services.payroll_disbursement_service import create_payroll_disbursement, generate_and_post_payroll_disbursement_journal
from app.services.payroll_insurance_funding_service import register_insurance_funding
from app.services.payroll_paid_income_service import allocation_from_record, read_paid_income, project_paid_income_month

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Isolated PostgreSQL')]


async def test_new_net_payments_freeze_gross_and_retry_does_not_duplicate(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,accrual,accounts,bank=await setup(db)
        journal=await receipt(db,company,user,accounts,bank,D(400))
        await register_insurance_funding(db,company_id=company.id,data=request(journal,bank,calc,D(400),'funded'),created_by=user.id)
        employee=await db.get(Employee,contract.employee_id)
        employee.payment_iban='UA213223130000026007233566001'
        await db.flush()
        # Install the migration's actual immutable guard in this isolated schema.
        import importlib.util
        spec=importlib.util.spec_from_file_location('paid_migration', 'alembic/versions/p13paidincome261115_paid_income.py')
        migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
        from unittest.mock import patch
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext
        connection=await db.connection()
        def install_guard(sync):
            operations=Operations(MigrationContext.configure(sync))
            # Metadata created the table; execute exactly the migration's guard DDL.
            with patch.object(migration.op, 'add_column'), patch.object(migration.op, 'create_check_constraint'), patch.object(migration.op, 'create_table'), patch.object(migration.op, 'execute', operations.execute):
                migration.upgrade()
        await connection.run_sync(install_guard)
        for number,amount in [(1,D(100)),(2,None)]:
            payment=await create_payroll_disbursement(db,company_id=company.id,payroll_calculation_id=calc.id,
                bank_account_id=bank.id,payment_date=date(2026,9,30),created_by=user.id,amount=amount,request_key=f'paid-income-{number}')
            posted=await generate_and_post_payroll_disbursement_journal(db,company_id=company.id,payroll_disbursement_id=payment.id,created_by=user.id)
            retry=await generate_and_post_payroll_disbursement_journal(db,company_id=company.id,payroll_disbursement_id=payment.id,created_by=user.id)
            assert retry.id==posted.id
        records=list((await db.scalars(select(PayrollPaidIncome).where(PayrollPaidIncome.company_id==company.id))).all())
        assert len(records)==2
        parts=[allocation_from_record(r)['allocation'] for r in records]
        statutory=await db.scalar(select(PayrollStatutoryResult).where(PayrollStatutoryResult.company_id==company.id,PayrollStatutoryResult.payroll_calculation_id==calc.id))
        assert sum(D(p['gross']) for p in parts)==statutory.gross_amount
        assert sum(D(p['net']) for p in parts)==statutory.net_amount
        assert sum(D(p['pit'])+D(p['levy']) for p in parts)==statutory.employee_withholding_amount
        report = await read_paid_income(db, company_id=company.id, payroll_calculation_id=calc.id)
        assert report['status'] == 'COMPLETE'
        assert report['gross_paid_by_month'] == {'2026-09': format(statutory.gross_amount, '.2f')}
        assert report['budget_remittances_used_as_withholding'] is False
        monthly=await project_paid_income_month(db,company_id=company.id,reporting_month='2026-09')
        assert monthly['status']=='COMPLETE'
        assert monthly['verified_gross_paid_total']==format(statutory.gross_amount,'.2f')
        assert monthly['official_form_approved'] is False
        other=await project_paid_income_month(db,company_id=company.id+999999,reporting_month='2026-09')
        assert other['rows']==[] and other['verified_gross_paid_total']=='0.00'
        for sql in ["UPDATE payroll_paid_income SET snapshot_json='{}' WHERE id=:id", "DELETE FROM payroll_paid_income WHERE id=:id"]:
            with pytest.raises(DBAPIError, match='immutable'):
                async with db.begin_nested():
                    await db.execute(text(sql), {'id':records[0].id})
        from app.services.payroll_disbursement_service import reverse_payroll_disbursement_journal
        await reverse_payroll_disbursement_journal(db, company_id=company.id,
            payroll_disbursement_id=payment.id, reversal_date=date(2026,9,30), reversed_by=user.id)
        after=await read_paid_income(db,company_id=company.id,payroll_calculation_id=calc.id)
        assert after['status']=='COMPLETE'
        first=next(r for r in records if r.payroll_disbursement_id!=payment.id)
        assert after['gross_paid_by_month']=={'2026-09':allocation_from_record(first)['allocation']['gross']}
        assert await db.scalar(select(func.count()).select_from(PayrollPaidIncome))==2
        await db.rollback()
