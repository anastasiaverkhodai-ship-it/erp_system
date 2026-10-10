import json
import os
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_source_locking import sources
from payroll_verified_evidence_helper import verified_evidence
from app.models.account import Account, AccountType, AccountNormalBalance
from app.models.bank_account import BankAccount
from app.models.employee_salary_rate import EmployeeSalaryRate
from app.models.payroll import PayrollPeriod
from app.models.payroll_statutory import PayrollStatutoryComponent as Component
from app.models.payroll_tax import PayrollEmployeeTaxProfile, PayrollStatutoryBaseRule
from app.schemas.time_attendance import AttendanceCreate
from app.services.time_attendance_service import create_attendance
from app.services.payroll_automatic_advance_service import create_automatic_payroll_advance
from app.services.payroll_advance_service import PayrollAdvanceError
from app.services.payroll_statutory_service import create_statutory_rate, resolve_statutory_rate, PayrollStatutoryError

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]


async def test_automatic_advance_keeps_minimum_taxes_evidence_and_retry_snapshot(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f, employment, _ = await sources(db)
        period = PayrollPeriod(company_id=f['company'], year=2026, month=9,
            start_date=date(2026,9,1), end_date=date(2026,9,30), status='draft', created_by=f['actor'])
        account = Account(company_id=f['company'], code='311', name='Bank', account_type=AccountType.ASSET,
            normal_balance=AccountNormalBalance.DEBIT, is_active=True, is_postable=True)
        db.add_all([period,account]);await db.flush()
        bank=BankAccount(company_id=f['company'],name='Bank',account_number='TEST-AUTO-ADVANCE',
            currency_code='UAH',accounting_account_id=account.id,is_active=True)
        db.add(bank);await db.flush()
        for day in range(1,16):
            work_date=date(2026,9,day)
            if work_date.weekday()>4:continue
            await create_attendance(db,company_id=f['company'],created_by=f['actor'],
                data=AttendanceCreate(employment_contract_id=employment.id,work_date=work_date,status='present',
                    actual_start_at=datetime(2026,9,day,9,tzinfo=ZoneInfo('Europe/Kyiv')),
                    actual_end_at=datetime(2026,9,day,17,tzinfo=ZoneInfo('Europe/Kyiv'))))
        args=dict(company_id=f['company'],payroll_period_id=period.id,employment_contract_id=employment.id,
            bank_account_id=bank.id,advance_percentage=Decimal('40'),payment_date=date(2026,9,20),created_by=f['actor'])
        with pytest.raises(PayrollAdvanceError,match='no statutory rate'):
            await create_automatic_payroll_advance(db,**args)
        for component,rate in [(Component.PERSONAL_INCOME_TAX,'.18'),(Component.MILITARY_LEVY,'.05'),(Component.UNIFIED_SOCIAL_CONTRIBUTION,'.22')]:
            await create_statutory_rate(db,company_id=f['company'],component=component,rate=Decimal(rate),
                effective_from=date(2026,1,1),effective_to=None,actor_user_id=f['actor'])
        rate_evidence = await verified_evidence(
            db,
            company_id=f['company'],
            employee_id=f['employee'],
            created_by=f['actor'],
            entitlement_type='individual_rate',
            entitlement_code=Component.UNIFIED_SOCIAL_CONTRIBUTION.value,
            valid_to=date(2026,12,31),
        )
        benefit_evidence = await verified_evidence(
            db,
            company_id=f['company'],
            employee_id=f['employee'],
            created_by=f['actor'],
            entitlement_type='benefit',
            entitlement_code='TEST',
        )
        individual=await create_statutory_rate(db,company_id=f['company'],employee_id=f['employee'],
            component=Component.UNIFIED_SOCIAL_CONTRIBUTION,rate=Decimal('.0841'),source_reference='Disability evidence TEST-001',tax_evidence_id=rate_evidence.id,
            effective_from=date(2026,1,1),effective_to=date(2026,12,31),actor_user_id=f['actor'])
        with pytest.raises(PayrollStatutoryError,match='overlap'):
            await create_statutory_rate(db,company_id=f['company'],employee_id=f['employee'],
                component=Component.UNIFIED_SOCIAL_CONTRIBUTION,rate=Decimal('.0841'),source_reference='TEST-002',tax_evidence_id=rate_evidence.id,
                effective_from=date(2026,6,1),effective_to=date(2026,12,31),actor_user_id=f['actor'])
        with pytest.raises(PayrollStatutoryError,match='Employee not found'):
            await create_statutory_rate(db,company_id=f['company'],employee_id=2147483647,
                component=Component.UNIFIED_SOCIAL_CONTRIBUTION,rate=Decimal('.0841'),source_reference='TEST',
                effective_from=date(2026,1,1),effective_to=None,actor_user_id=f['actor'])
        db.add(PayrollEmployeeTaxProfile(company_id=f['company'],employee_id=f['employee'],
            employment_contract_id=employment.id,category='benefit_eligible',benefit_code='TEST',tax_evidence_id=benefit_evidence.id,
            effective_from=date(2026,1,1),created_by=f['actor']))
        db.add(PayrollStatutoryBaseRule(company_id=f['company'],component=Component.PERSONAL_INCOME_TAX.value,
            tax_profile_category='benefit_eligible',base_mode='gross_after_benefit',benefit_income_limit=Decimal('20000'),benefit_amount=Decimal('1000'),
            rule_code='TEST-BENEFIT',rule_version='1',effective_from=date(2026,1,1),created_by=f['actor']))
        await db.flush()
        individual.effective_from=date(2026,9,10)
        await db.flush()
        with pytest.raises(PayrollAdvanceError,match='dated income allocation'):
            await create_automatic_payroll_advance(db,**args)
        individual.effective_from=date(2026,1,1)
        await db.flush()
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from types import SimpleNamespace
        from app.api.v1 import payroll_advances
        from app.api.deps import get_current_user
        from app.core.database import get_db
        from app.models.role import Role
        from app.models.permission import Permission
        from app.models.rbac import role_permissions
        from app.models.user_company_role import UserCompanyRole
        from app.models.payroll_advance import PayrollAdvance
        role=Role(name='Auto advance manager'); permission=Permission(name='employees.manage')
        db.add_all([role,permission]);await db.flush()
        await db.execute(role_permissions.insert(),[dict(role_id=role.id,permission_id=permission.id)])
        db.add(UserCompanyRole(user_id=f['actor'],company_id=f['company'],role_id=role.id))
        await db.commit()
        api=FastAPI();api.include_router(payroll_advances.router)
        async def session():yield db
        api.dependency_overrides[get_db]=session
        api.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=f['actor'])
        async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as client:
            payload=dict(payroll_period_id=period.id,employment_contract_id=employment.id,
                bank_account_id=bank.id,advance_percentage='40',payment_date='2026-09-20')
            rejected=await client.post(f"/companies/{f['company']}/payroll-advances",json={**payload,'minimum_due_amount':'0'})
            assert rejected.status_code==422,rejected.text
            created=await client.post(f"/companies/{f['company']}/payroll-advances",json=payload)
            assert created.status_code==200,created.text
        advance=await db.get(PayrollAdvance,created.json()['id'])
        assert advance.calculated_amount==Decimal('11000')
        assert advance.minimum_due_amount==Decimal('8470')
        assert advance.paid_amount==Decimal('8470')
        snapshot=json.loads(advance.calculation_snapshot_json)
        assert snapshot['social_benefit_applied'] is False
        lines={line['component']:line for line in snapshot['tax_lines']}
        assert Decimal(lines[Component.PERSONAL_INCOME_TAX.value]['amount'])==Decimal('1980')
        assert Decimal(lines[Component.MILITARY_LEVY.value]['amount'])==Decimal('550')
        assert Decimal(lines[Component.UNIFIED_SOCIAL_CONTRIBUTION.value]['amount'])==Decimal('925.10')
        assert lines[Component.UNIFIED_SOCIAL_CONTRIBUTION.value]['source_rate_id']==individual.id
        assert lines[Component.UNIFIED_SOCIAL_CONTRIBUTION.value]['source_tax_evidence_id']==rate_evidence.id
        assert snapshot['tax_profile_evidence_id']==benefit_evidence.id
        original=advance.calculation_snapshot_json
        salary=await db.scalar(select(EmployeeSalaryRate).where(EmployeeSalaryRate.employment_contract_id==employment.id))
        salary.amount=Decimal('33000');await db.flush()
        retry=await create_automatic_payroll_advance(db,**args)
        assert retry.id==advance.id and retry.calculation_snapshot_json==original
        with pytest.raises(PayrollAdvanceError,match='different request'):
            await create_automatic_payroll_advance(db,**{**args,'advance_percentage':Decimal('50')})
        rate=await resolve_statutory_rate(db,company_id=f['company'],employee_id=f['employee'],
            component=Component.UNIFIED_SOCIAL_CONTRIBUTION,effective_date=date(2027,1,1))
        assert rate.rate==Decimal('.22')
        from test_payroll_settlement_migrations import assert_history_preserved
        await assert_history_preserved(db,'13d4a7b8c010_payroll_advance_snapshot_and_individual_rates.py')
