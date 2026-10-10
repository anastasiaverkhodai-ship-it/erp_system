import os
from datetime import date, datetime, UTC
from decimal import Decimal as D
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_vacation_postgresql import _seed_identity
from payroll_verified_evidence_helper import verified_evidence
from app.models.employment_contract import EmploymentContract
from app.models.payroll import PayrollPeriod, PayrollInput, PayrollCalculation
from app.models.payroll_statutory import PayrollStatutoryComponent as Component, PayrollStatutoryResultLine as Line
from app.models.payroll_tax import PayrollStatutoryBaseRule
from app.services.payroll_statutory_service import create_statutory_rate, calculate_payroll_statutory_result, PayrollStatutoryValidationError

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]


async def setup_month(db, amounts, floor, ceiling, rate=D('.22')):
    company,user,employee,primary=await _seed_identity(db)
    primary.employment_kind='primary'
    secondary=EmploymentContract(company_id=company.id,employee_id=employee.id,contract_number='SECOND',
        contract_type='standard',work_arrangement='part_time',employment_kind='internal_secondary',
        start_date=date(2026,1,1),status='active',created_by=user.id)
    db.add(secondary);await db.flush()
    period=PayrollPeriod(company_id=company.id,year=2026,month=9,start_date=date(2026,9,1),
        end_date=date(2026,9,30),status='finalized',created_by=user.id,finalized_by=user.id,finalized_at=datetime.now(UTC))
    db.add(period);await db.flush()
    calcs=[]
    for contract,amount in zip((primary,secondary),amounts):
        source=PayrollInput(company_id=company.id,payroll_period_id=period.id,employment_contract_id=contract.id,created_by=user.id)
        db.add(source);await db.flush()
        calc=PayrollCalculation(company_id=company.id,payroll_period_id=period.id,payroll_input_id=source.id,
            employment_contract_id=contract.id,gross_amount=D(amount),currency_code='UAH',calculated_by=user.id)
        db.add(calc);await db.flush();calcs.append(calc)
    for component,value in [(Component.PERSONAL_INCOME_TAX,D('.18')),(Component.MILITARY_LEVY,D('.05')),
                            (Component.UNIFIED_SOCIAL_CONTRIBUTION,rate)]:
        await create_statutory_rate(db,company_id=company.id,component=component,rate=value,
            effective_from=date(2026,1,1),effective_to=None,actor_user_id=user.id)
    for kind in ('primary','internal_secondary'):
        db.add(PayrollStatutoryBaseRule(company_id=company.id,component=Component.UNIFIED_SOCIAL_CONTRIBUTION.value,
            employment_kind=kind,base_mode='gross',benefit_amount=0,
            minimum_base_amount=D(floor) if floor and kind=='primary' else None,
            maximum_base_amount=D(ceiling) if ceiling else None,exemption_applies=False,
            rule_code='AGGREGATE_TEST',rule_version='1',effective_from=date(2026,1,1),created_by=user.id))
    await db.flush()
    return company,user,calcs


@pytest.mark.parametrize('amounts,floor,ceiling,expected_bases,expected_total',[
    (('1000','500'),'8000','10000',('7500','500'),'1760'),
    (('8000','7000'),None,'10000',('8000','2000'),'2200'),
    (('2.30','7.71'),None,None,('2.30','7.71'),'2.20'),
])
async def test_employee_usc_limits_and_rounding_independent_of_call_order(employee_engine,amounts,floor,ceiling,expected_bases,expected_total):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,calcs=await setup_month(db,amounts,floor,ceiling)
        results={}
        for index in (1,0):
            results[index]=await calculate_payroll_statutory_result(db,company_id=company.id,
                payroll_calculation_id=calcs[index].id,actor_user_id=user.id)
        total=D(0)
        for index in (0,1):
            line=await db.scalar(select(Line).where(Line.payroll_statutory_result_id==results[index].id,
                Line.component==Component.UNIFIED_SOCIAL_CONTRIBUTION.value))
            assert line.base_amount==D(expected_bases[index])
            total+=line.amount
        assert total==D(expected_total)
        assert (await calculate_payroll_statutory_result(db,company_id=company.id,
            payroll_calculation_id=calcs[0].id,actor_user_id=user.id)).id==results[0].id


async def test_missing_employee_contract_calculation_blocks_tax(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,calcs=await setup_month(db,('1000','500'),'8000','10000')
        calcs[1].revision=2;await db.flush()
        with pytest.raises(PayrollStatutoryValidationError,match='all employee contracts'):
            await calculate_payroll_statutory_result(db,company_id=company.id,payroll_calculation_id=calcs[0].id,actor_user_id=user.id)


async def test_changed_peer_is_not_combined_with_saved_usc(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,calcs=await setup_month(db,('8000','7000'),None,'10000')
        await calculate_payroll_statutory_result(db,company_id=company.id,payroll_calculation_id=calcs[1].id,actor_user_id=user.id)
        calcs[0].gross_amount=D('9000');await db.flush()
        with pytest.raises(PayrollStatutoryValidationError,match='Saved peer tax'):
            await calculate_payroll_statutory_result(db,company_id=company.id,payroll_calculation_id=calcs[0].id,actor_user_id=user.id)


@pytest.mark.parametrize('amounts,limit,benefit,expected_benefit,expected_pit',[
    (('1000','1500'),'3000','1600','1600','162'),
    (('2000','1500'),'3000','1600','0','630'),
])
async def test_employee_benefit_is_once_and_subject_to_aggregate_income(employee_engine,amounts,limit,benefit,expected_benefit,expected_pit):
    from app.models.payroll_tax import PayrollEmployeeTaxProfile
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,calcs=await setup_month(db,amounts,None,None)
        employment=await db.get(EmploymentContract,calcs[0].employment_contract_id)
        evidence = await verified_evidence(
            db,
            company_id=company.id,
            employee_id=employment.employee_id,
            created_by=user.id,
            entitlement_type='benefit',
            entitlement_code='Signed entitlement',
        )
        profile=PayrollEmployeeTaxProfile(company_id=company.id,employee_id=employment.employee_id,
            employment_contract_id=employment.id,category='benefit_eligible',benefit_code='Signed entitlement',tax_evidence_id=evidence.id,
            effective_from=date(2026,1,1),created_by=user.id)
        db.add(profile)
        db.add(PayrollStatutoryBaseRule(company_id=company.id,component=Component.PERSONAL_INCOME_TAX.value,
            tax_profile_category='benefit_eligible',base_mode='gross_after_benefit',benefit_amount=D(benefit),
            benefit_income_limit=D(limit),rule_code='BENEFIT_TEST',rule_version='1',effective_from=date(2026,1,1),created_by=user.id))
        await db.flush()
        results=[]
        for source in reversed(calcs):
            results.append(await calculate_payroll_statutory_result(db,company_id=company.id,
                payroll_calculation_id=source.id,actor_user_id=user.id))
        rows=list((await db.scalars(select(Line).where(Line.payroll_statutory_result_id.in_([r.id for r in results]),
            Line.component==Component.PERSONAL_INCOME_TAX.value))).all())
        assert sum(r.benefit_amount_applied for r in rows)==D(expected_benefit)
        assert sum(r.amount for r in rows)==D(expected_pit)
        assert all(r.benefit_income_limit_applied==D(limit) and r.source_tax_profile_id==profile.id for r in rows)
        assert all(r.employee_gross_amount==sum(map(D,amounts)) for r in rows)


@pytest.mark.parametrize('rate,amounts,expected',[
    (D('.0841'),('1000','500'),'126.15'),
    (D('.22'),('0','0'),'0'),
])
async def test_employee_usc_floor_excludes_disability_and_zero_income(employee_engine,rate,amounts,expected):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,calcs=await setup_month(db,amounts,'8000','10000',rate=rate)
        total=D(0)
        for calculation in calcs:
            result=await calculate_payroll_statutory_result(db,company_id=company.id,
                payroll_calculation_id=calculation.id,actor_user_id=user.id)
            total+=result.employer_contribution_amount
        assert total==D(expected)
        from test_payroll_settlement_migrations import assert_history_preserved
        await assert_history_preserved(db,'13d4a7b8c011_employee_tax_benefit_limits.py')


@pytest.mark.parametrize('kind,rate,amount,expected',[
    ('primary',D('.22'),'1000','1760'),
    ('primary',D('.0841'),'1000','84.10'),
    ('external_secondary',D('.22'),'1000','220'),
    ('primary',D('.22'),'0','0'),
])
async def test_single_contract_usc_floor_eligibility(employee_engine,kind,rate,amount,expected):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,calcs=await setup_month(db,(amount,'0'),'8000','10000',rate=rate)
        secondary=await db.get(EmploymentContract,calcs[1].employment_contract_id)
        secondary.status='cancelled'
        primary=await db.get(EmploymentContract,calcs[0].employment_contract_id)
        primary.employment_kind=kind
        await db.flush()
        result=await calculate_payroll_statutory_result(db,company_id=company.id,
            payroll_calculation_id=calcs[0].id,actor_user_id=user.id)
        assert result.employer_contribution_amount==D(expected)


@pytest.mark.parametrize('source',['profile','base_rule'])
async def test_midmonth_tax_policy_is_not_applied_retroactively(employee_engine,source):
    from app.models.payroll_tax import PayrollEmployeeTaxProfile
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,calcs=await setup_month(db,('1000','500'),'8000','10000')
        contract=await db.get(EmploymentContract,calcs[0].employment_contract_id)
        if source=='profile':
            db.add(PayrollEmployeeTaxProfile(company_id=company.id,employee_id=contract.employee_id,
                employment_contract_id=contract.id,category='standard',effective_from=date(2026,9,15),created_by=user.id))
        else:
            rule=await db.scalar(select(PayrollStatutoryBaseRule).where(
                PayrollStatutoryBaseRule.company_id==company.id,PayrollStatutoryBaseRule.employment_kind=='primary'))
            rule.effective_from=date(2026,9,15)
        await db.flush()
        with pytest.raises(PayrollStatutoryValidationError,match='dated allocation'):
            await calculate_payroll_statutory_result(db,company_id=company.id,
                payroll_calculation_id=calcs[0].id,actor_user_id=user.id)


@pytest.mark.parametrize('invalid',['currency','revision'])
async def test_single_contract_requires_current_uah_earnings(employee_engine,invalid):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,calcs=await setup_month(db,('1000','0'),None,None)
        secondary=await db.get(EmploymentContract,calcs[1].employment_contract_id)
        secondary.status='cancelled'
        if invalid=='currency':
            calcs[0].currency_code='USD'
        else:
            calcs[0].revision=2
        await db.flush()
        with pytest.raises(PayrollStatutoryValidationError,match='UAH|correction is not linked'):
            await calculate_payroll_statutory_result(db,company_id=company.id,
                payroll_calculation_id=calcs[0].id,actor_user_id=user.id)


async def test_ended_contract_uses_policy_on_last_employment_day(employee_engine):
    from app.models.payroll_tax import PayrollEmployeeTaxProfile
    from app.models.payroll_statutory import PayrollStatutoryRate
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,calcs=await setup_month(db,('1000','0'),None,None)
        secondary=await db.get(EmploymentContract,calcs[1].employment_contract_id)
        secondary.status='cancelled'
        primary=await db.get(EmploymentContract,calcs[0].employment_contract_id)
        primary.end_date=date(2026,9,15)
        primary.status='ended'
        for rate in (await db.scalars(select(PayrollStatutoryRate).where(PayrollStatutoryRate.company_id==company.id))).all():
            rate.effective_to=primary.end_date
        db.add(PayrollEmployeeTaxProfile(company_id=company.id,employee_id=primary.employee_id,
            employment_contract_id=primary.id,category='standard',effective_from=date(2026,1,1),
            effective_to=primary.end_date,created_by=user.id))
        await db.flush()
        result=await calculate_payroll_statutory_result(db,company_id=company.id,
            payroll_calculation_id=calcs[0].id,actor_user_id=user.id)
        assert result.net_amount==D('770')
        assert result.employer_contribution_amount==D('220')


async def test_midmonth_company_rate_requires_dated_income(employee_engine):
    from app.models.payroll_statutory import PayrollStatutoryRate
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,calcs=await setup_month(db,('1000','500'),None,None)
        old=await db.scalar(select(PayrollStatutoryRate).where(
            PayrollStatutoryRate.company_id==company.id,PayrollStatutoryRate.component==Component.MILITARY_LEVY.value))
        old.effective_to=date(2026,9,14)
        await db.flush()
        await create_statutory_rate(db,company_id=company.id,component=Component.MILITARY_LEVY,
            rate=D('.06'),effective_from=date(2026,9,15),effective_to=None,actor_user_id=user.id)
        with pytest.raises(PayrollStatutoryValidationError,match='dated allocation'):
            await calculate_payroll_statutory_result(db,company_id=company.id,
                payroll_calculation_id=calcs[0].id,actor_user_id=user.id)


@pytest.mark.parametrize('invalid',['cancelled','outside_period'])
async def test_contract_must_cover_payroll_income_period(employee_engine,invalid):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,calcs=await setup_month(db,('1000','0'),None,None)
        primary=await db.get(EmploymentContract,calcs[0].employment_contract_id)
        if invalid=='cancelled':
            primary.status='cancelled'
        else:
            primary.start_date=date(2026,10,1)
        await db.flush()
        with pytest.raises(PayrollStatutoryValidationError,match='does not cover'):
            await calculate_payroll_statutory_result(db,company_id=company.id,
                payroll_calculation_id=calcs[0].id,actor_user_id=user.id)
