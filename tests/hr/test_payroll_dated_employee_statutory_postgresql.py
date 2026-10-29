import os
from datetime import date, datetime, timezone
from decimal import Decimal as D
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_vacation_postgresql import _seed_identity
from payroll_verified_evidence_helper import verified_evidence
from app.models.employee_salary_rate import EmployeeSalaryRate
from app.models.payroll import PayrollPeriod, PayrollInput, PayrollInputSalarySlice
from app.models.payroll_statutory import PayrollStatutoryComponent as Component, PayrollStatutoryResultLine as Line, PayrollStatutoryLineSource as Source
from app.services.payroll_statutory_service import create_statutory_rate, calculate_payroll_statutory_result
from app.services.payroll_calculation_service import calculate_payroll_input
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]


async def setup(db):
    company,user,employee,contract=await _seed_identity(db)
    contract.employment_kind='primary'
    period=PayrollPeriod(company_id=company.id,year=2026,month=9,start_date=date(2026,9,1),end_date=date(2026,9,30),
        status='finalized',created_by=user.id,finalized_by=user.id,finalized_at=datetime.now(timezone.utc))
    rate=EmployeeSalaryRate(company_id=company.id,employment_contract_id=contract.id,rate_type='monthly',
        amount=22000,currency_code='UAH',effective_from=date(2026,1,1),created_by=user.id)
    db.add_all([period,rate]);await db.flush()
    pi=PayrollInput(company_id=company.id,payroll_period_id=period.id,employment_contract_id=contract.id,
        monthly_norm_minutes=10560,scheduled_minutes=10560,worked_minutes=10560,created_by=user.id)
    db.add(pi);await db.flush()
    for start,end,minutes in [(date(2026,9,1),date(2026,9,14),4800),(date(2026,9,15),date(2026,9,30),5760)]:
        db.add(PayrollInputSalarySlice(company_id=company.id,payroll_input_id=pi.id,salary_rate_id=rate.id,
            salary_rate_type='monthly',salary_rate_amount=22000,currency_code='UAH',effective_from=start,effective_to=end,
            scheduled_minutes=minutes,worked_minutes=minutes))
    for component,value in [(Component.PERSONAL_INCOME_TAX,'.18'),(Component.MILITARY_LEVY,'.05'),(Component.UNIFIED_SOCIAL_CONTRIBUTION,'.22')]:
        await create_statutory_rate(db,company_id=company.id,component=component,rate=D(value),
            effective_from=date(2026,1,1),effective_to=None,actor_user_id=user.id)
    evidence=await verified_evidence(db,company_id=company.id,employee_id=employee.id,created_by=user.id,
        entitlement_type='individual_rate',entitlement_code=Component.UNIFIED_SOCIAL_CONTRIBUTION.value,
        valid_from=date(2026,9,15),valid_to=date(2026,12,31))
    await create_statutory_rate(db,company_id=company.id,component=Component.UNIFIED_SOCIAL_CONTRIBUTION,
        rate=D('.0841'),effective_from=date(2026,9,15),effective_to=date(2026,12,31),employee_id=employee.id,
        tax_evidence_id=evidence.id,source_reference='Received verified disability document',actor_user_id=user.id)
    await db.flush()
    calc=await calculate_payroll_input(db,company_id=company.id,payroll_input_id=pi.id,calculated_by=user.id)
    return company,user,employee,contract,calc


async def test_individual_midmonth_rate_uses_actual_salary_slices(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,_,_,calc=await setup(db)
        result=await calculate_payroll_statutory_result(db,company_id=company.id,payroll_calculation_id=calc.id,actor_user_id=user.id)
        assert result.gross_amount==22000
        assert result.employer_contribution_amount==D('3209.20') # 10000*.22 + 12000*.0841
        assert result.net_amount==16940
        lines=list((await db.scalars(select(Line).where(Line.payroll_statutory_result_id==result.id))).all())
        usc=[x for x in lines if x.component=='unified_social_contribution']
        assert {(x.base_amount,x.rate) for x in usc}=={(D(10000),D('.22')),(D(12000),D('.0841'))}
        assert await db.scalar(select(func.count(Source.id)).where(Source.company_id==company.id))==6
        assert (await calculate_payroll_statutory_result(db,company_id=company.id,payroll_calculation_id=calc.id,actor_user_id=user.id)).id==result.id


async def test_midmonth_benefit_and_usc_limits_are_monthly(employee_engine):
    from app.models.payroll_tax import PayrollEmployeeTaxProfile, PayrollStatutoryBaseRule
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,employee,contract,calc=await setup(db)
        evidence=await verified_evidence(db,company_id=company.id,employee_id=employee.id,created_by=user.id,
            entitlement_type='benefit',entitlement_code='MONTHLY_TEST',valid_from=date(2026,9,15),valid_to=date(2026,12,31))
        db.add(PayrollEmployeeTaxProfile(company_id=company.id,employee_id=employee.id,
            employment_contract_id=contract.id,category='benefit_eligible',benefit_code='MONTHLY_TEST',
            tax_evidence_id=evidence.id,effective_from=date(2026,9,15),effective_to=date(2026,12,31),created_by=user.id))
        db.add(PayrollStatutoryBaseRule(company_id=company.id,component='personal_income_tax',
            tax_profile_category='benefit_eligible',base_mode='gross_after_benefit',benefit_amount=1600,
            benefit_income_limit=30000,rule_code='MONTHLY_TEST',rule_version='1',effective_from=date(2026,1,1),created_by=user.id))
        db.add(PayrollStatutoryBaseRule(company_id=company.id,component='unified_social_contribution',
            employment_kind='primary',base_mode='gross',benefit_amount=0,minimum_base_amount=30000,
            maximum_base_amount=40000,exemption_applies=False,rule_code='USC_TEST',rule_version='1',
            effective_from=date(2026,1,1),created_by=user.id))
        await db.flush()
        result=await calculate_payroll_statutory_result(db,company_id=company.id,payroll_calculation_id=calc.id,actor_user_id=user.id)
        rows=list((await db.scalars(select(Line).where(Line.payroll_statutory_result_id==result.id))).all())
        pit=[x for x in rows if x.component=='personal_income_tax']
        assert sum(x.benefit_amount_applied for x in pit)==D(1600)
        assert sum(x.amount for x in pit)==D(3672)
        assert result.employer_contribution_amount==D('3209.20') # No full-month floor across a reduced rate transition.
        assert result.net_amount==17228


async def test_midmonth_usc_ceiling_consumed_once(employee_engine):
    from app.models.payroll_tax import PayrollStatutoryBaseRule
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,employee,contract,calc=await setup(db)
        db.add(PayrollStatutoryBaseRule(company_id=company.id,component='unified_social_contribution',
            employment_kind='primary',base_mode='gross',benefit_amount=0,maximum_base_amount=15000,
            exemption_applies=False,rule_code='USC_CEILING',rule_version='1',effective_from=date(2026,1,1),created_by=user.id))
        await db.flush()
        result=await calculate_payroll_statutory_result(db,company_id=company.id,payroll_calculation_id=calc.id,actor_user_id=user.id)
        rows=list((await db.scalars(select(Line).where(Line.payroll_statutory_result_id==result.id,
            Line.component=='unified_social_contribution'))).all())
        assert sum(x.base_amount for x in rows)==15000
        assert result.employer_contribution_amount==D('2620.50')


@pytest.mark.parametrize('reverse_order',[False,True])
async def test_two_contract_dated_tax_is_independent_of_call_order(employee_engine,reverse_order):
    from app.models.employment_contract import EmploymentContract
    from app.models.payroll_tax import PayrollStatutoryBaseRule
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,employee,primary,first=await setup(db)
        secondary=EmploymentContract(company_id=company.id,employee_id=employee.id,contract_number='SECOND',
            contract_type='standard',work_arrangement='part_time',employment_kind='internal_secondary',
            start_date=date(2026,1,1),status='active',created_by=user.id)
        db.add(secondary);await db.flush()
        rate=EmployeeSalaryRate(company_id=company.id,employment_contract_id=secondary.id,rate_type='monthly',
            amount=11000,currency_code='UAH',effective_from=date(2026,1,1),created_by=user.id)
        pi=PayrollInput(company_id=company.id,payroll_period_id=first.payroll_period_id,employment_contract_id=secondary.id,
            monthly_norm_minutes=10560,scheduled_minutes=10560,worked_minutes=10560,created_by=user.id)
        db.add_all([rate,pi]);await db.flush()
        for start,end,minutes in [(date(2026,9,1),date(2026,9,14),4800),(date(2026,9,15),date(2026,9,30),5760)]:
            db.add(PayrollInputSalarySlice(company_id=company.id,payroll_input_id=pi.id,salary_rate_id=rate.id,
                salary_rate_type='monthly',salary_rate_amount=11000,currency_code='UAH',effective_from=start,effective_to=end,
                scheduled_minutes=minutes,worked_minutes=minutes))
        for kind in ('primary','internal_secondary'):
            db.add(PayrollStatutoryBaseRule(company_id=company.id,component='unified_social_contribution',
                employment_kind=kind,base_mode='gross',benefit_amount=0,maximum_base_amount=25000,
                exemption_applies=False,rule_code='EMPLOYEE_CEILING',rule_version='1',effective_from=date(2026,1,1),created_by=user.id))
        await db.flush()
        second=await calculate_payroll_input(db,company_id=company.id,payroll_input_id=pi.id,calculated_by=user.id,
            minimum_wage_context=dict(full_time_norm_minutes=10560,norm_reference="Confirmed full-time September calendar"))
        results={}
        for calc in ([second,first] if reverse_order else [first,second]):
            results[calc.id]=await calculate_payroll_statutory_result(db,company_id=company.id,
                payroll_calculation_id=calc.id,actor_user_id=user.id)
        assert results[first.id].employer_contribution_amount==D('3209.20')
        assert results[second.id].employer_contribution_amount==D(660)
        assert sum(row.net_amount for row in results.values())==D(25410)
        assert (await calculate_payroll_statutory_result(db,company_id=company.id,
            payroll_calculation_id=first.id,actor_user_id=user.id)).id==results[first.id].id


async def test_new_dated_results_use_real_immutable_seals(employee_engine):
    from importlib.util import spec_from_file_location,module_from_spec
    from unittest.mock import patch
    from alembic import op
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    statements=[]
    for filename in ('13d4a7b8c045_dated_statutory_immutability.py','13d4a7b8c046_dated_statutory_seals.py'):
        spec=spec_from_file_location(filename,'alembic/versions/'+filename)
        migration=module_from_spec(spec);spec.loader.exec_module(migration)
        with patch.object(op,'execute',lambda sql:statements.append(sql)):
            migration.upgrade()
    async with employee_engine.begin() as conn:
        for sql in statements:
            if 'CREATE TABLE payroll_dated_statutory_seals' not in sql:
                await conn.execute(text(sql))
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,_,_,calc=await setup(db)
        result=await calculate_payroll_statutory_result(db,company_id=company.id,payroll_calculation_id=calc.id,actor_user_id=user.id)
        for statement in (
            'UPDATE payroll_statutory_results SET net_amount=net_amount+1 WHERE id=:id',
            'DELETE FROM payroll_statutory_line_sources WHERE company_id=:company',
            'INSERT INTO payroll_statutory_result_lines (company_id,payroll_statutory_result_id,line_no,component,base_amount,rate,amount,currency_code,source_rate_id) SELECT company_id,payroll_statutory_result_id,99,component,base_amount,rate,amount,currency_code,source_rate_id FROM payroll_statutory_result_lines WHERE payroll_statutory_result_id=:id LIMIT 1',
        ):
            with pytest.raises(DBAPIError,match='[Ii]mmutable|[Ss]ealed'):
                async with db.begin_nested():
                    await db.execute(text(statement),dict(id=result.id,company=company.id))
        assert (await calculate_payroll_statutory_result(db,company_id=company.id,payroll_calculation_id=calc.id,actor_user_id=user.id)).id==result.id
