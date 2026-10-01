import asyncio
import os
from datetime import date
from decimal import Decimal
from uuid import uuid4
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.models.company import Company
from app.models.employee import Employee
from app.models.employee_salary_rate import EmployeeSalaryRate
from app.models.employment_contract import EmploymentContract
from app.models.hr_change import HRChange
from app.models.user import User
from app.schemas.employee_salary_rate import SalaryRateCreate, SalaryRateUpdate
from app.services.employee_salary_rate_service import create_salary_rate, update_salary_rate
from app.services.hr_change_service import list_changes
pytestmark = pytest.mark.asyncio

def _database_url():
    from app.core.config import settings

    url = settings.database_url
    if not url or 'postgresql' not in url:
        raise RuntimeError('Real PostgreSQL database_url is required')

    if url.startswith('postgresql://'):
        url = 'postgresql+asyncpg://' + url[len('postgresql://'):]
    elif url.startswith('postgresql+psycopg://'):
        url = 'postgresql+asyncpg://' + url[len('postgresql+psycopg://'):]

    return url

def _column_default(model, name):
    col = model.__table__.c[name]
    if col.default is not None:
        return col.default.arg
    return None

def _required_values(model, overrides):
    values = dict(overrides)
    table = model.__table__
    for col in table.columns:
        if col.name in values:
            continue
        if col.primary_key and col.autoincrement:
            continue
        if col.nullable:
            continue
        if col.default is not None or col.server_default is not None:
            continue
        python_type = None
        try:
            python_type = col.type.python_type
        except Exception:
            pass
        n = col.name.lower()
        if n.endswith('_id'):
            continue
        if python_type is str:
            values[col.name] = f'e2e-{uuid4().hex[:12]}'
        elif python_type is bool:
            values[col.name] = True
        elif python_type is int:
            values[col.name] = 1
        elif python_type is date:
            values[col.name] = date(2026, 1, 1)
        elif python_type is Decimal:
            values[col.name] = Decimal('1.00')
    return values

async def _flush_with_missing_fk_repair(session, model, values):
    obj = model(**values)
    session.add(obj)
    await session.flush()
    return obj

async def _seed(session):
    token = uuid4().hex[:10]
    company_values = _required_values(Company, {'name': f'Salary E2E {token}'})
    company = Company(**company_values)
    session.add(company)
    await session.flush()
    user_values = _required_values(
        User,
        {
            'email': f'salary-e2e-{token}@example.com',
            'password_hash': 'not-a-real-password',
            'first_name': 'Salary',
            'last_name': 'E2E',
        },
    )
    for candidate in ('email', 'username'):
        if candidate in User.__table__.c and candidate not in user_values:
            user_values[candidate] = f'salary-e2e-{token}@example.com' if candidate == 'email' else f'salary-e2e-{token}'
    if 'hashed_password' in User.__table__.c and 'hashed_password' not in user_values:
        user_values['hashed_password'] = 'not-a-real-password'
    if 'is_active' in User.__table__.c:
        user_values['is_active'] = True
    user = User(**user_values)
    session.add(user)
    await session.flush()
    employee_values = _required_values(Employee, {'company_id': company.id, 'employee_number': f'E2E-{token}', 'first_name': 'Salary', 'last_name': 'E2E', 'hire_date': date(2026, 1, 1), 'created_by': user.id})
    if 'status' in Employee.__table__.c:
        employee_values['status'] = 'active'
    employee = Employee(**employee_values)
    session.add(employee)
    await session.flush()
    contract_values = _required_values(EmploymentContract, {'company_id': company.id, 'employee_id': employee.id, 'contract_number': f'C-{token}', 'contract_type': 'standard', 'work_arrangement': 'full_time', 'start_date': date(2026, 1, 1), 'end_date': None, 'status': 'active', 'created_by': user.id})
    contract = EmploymentContract(**contract_values)
    session.add(contract)
    await session.flush()
    return (company, user, employee, contract)

def _create_payload(contract_id, start, end=None, amount='5000.00'):
    return SalaryRateCreate(employment_contract_id=contract_id, rate_type='monthly', amount=Decimal(amount), currency_code='EUR', effective_from=start, effective_to=end)

async def test_salary_rate_real_postgresql_crud_history_and_boundaries():
    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with Session() as session:
            async with session.begin():
                company, user, employee, contract = await _seed(session)
                rate = await create_salary_rate(session, company_id=company.id, data=_create_payload(contract.id, date(2026, 1, 1), date(2026, 6, 30)), created_by=user.id)
                assert rate.id is not None
                assert rate.company_id == company.id
                assert rate.employment_contract_id == contract.id
                assert Decimal(rate.amount) == Decimal('5000.00')
                assert rate.currency_code == 'EUR'
                history = await list_changes(session, company.id, 'salary_rate', rate.id)
                assert len(history) >= 1
                assert history[-1].entity_type == 'salary_rate'
                adjacent = await create_salary_rate(session, company_id=company.id, data=_create_payload(contract.id, date(2026, 7, 1), None, '5500.00'), created_by=user.id)
                assert adjacent.id != rate.id
                with pytest.raises(Exception):
                    await create_salary_rate(session, company_id=company.id, data=_create_payload(contract.id, date(2026, 6, 30), date(2026, 8, 1), '6000.00'), created_by=user.id)
                await session.rollback()
        async with Session() as session:
            async with session.begin():
                company, user, employee, contract = await _seed(session)
                rate = await create_salary_rate(session, company_id=company.id, data=_create_payload(contract.id, date(2026, 1, 1), None), created_by=user.id)
                before = Decimal(rate.amount)
                updated = await update_salary_rate(session, company_id=company.id, salary_rate_id=rate.id, data=SalaryRateUpdate(amount=Decimal('5200.00')), changed_by=user.id)
                assert Decimal(updated.amount) == Decimal('5200.00')
                assert Decimal(updated.amount) != before
                history = await list_changes(session, company.id, 'salary_rate', rate.id)
                assert len(history) >= 2
                await session.rollback()
    finally:
        await engine.dispose()

async def test_salary_rate_company_isolation_and_contract_window():
    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with Session() as session:
            async with session.begin():
                company1, user1, employee1, contract1 = await _seed(session)
                company2, user2, employee2, contract2 = await _seed(session)
                with pytest.raises(Exception):
                    await create_salary_rate(session, company_id=company2.id, data=_create_payload(contract1.id, date(2026, 1, 1), None), created_by=user2.id)
                await session.rollback()
        async with Session() as session:
            async with session.begin():
                company, user, employee, contract = await _seed(session)
                contract.end_date = date(2026, 12, 31)
                contract.status = 'ended'
                await session.flush()
                with pytest.raises(Exception):
                    await create_salary_rate(session, company_id=company.id, data=_create_payload(contract.id, date(2026, 1, 1), None), created_by=user.id)
                await session.rollback()
    finally:
        await engine.dispose()

async def test_salary_rate_concurrent_overlap_real_postgresql():
    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    company_id = None
    contract_id = None
    user_id = None
    try:
        async with Session() as setup:
            async with setup.begin():
                company, user, employee, contract = await _seed(setup)
                company_id = company.id
                contract_id = contract.id
                user_id = user.id

        async def attempt(amount):
            async with Session() as session:
                try:
                    async with session.begin():
                        row = await create_salary_rate(session, company_id=company_id, data=_create_payload(contract_id, date(2026, 1, 1), None, amount), created_by=user_id)
                        await session.flush()
                        return ('ok', row.id)
                except Exception as exc:
                    await session.rollback()
                    return ('error', type(exc).__name__)
        results = await asyncio.gather(attempt('5000.00'), attempt('6000.00'))
        successes = [x for x in results if x[0] == 'ok']
        failures = [x for x in results if x[0] == 'error']
        assert len(successes) == 1, results
        assert len(failures) == 1, results
        async with Session() as verify:
            rows = list((await verify.scalars(select(EmployeeSalaryRate).where(EmployeeSalaryRate.company_id == company_id, EmployeeSalaryRate.employment_contract_id == contract_id))).all())
            assert len(rows) == 1
    finally:
        async with Session() as cleanup:
            try:
                async with cleanup.begin():
                    if company_id is not None:
                        await cleanup.execute(delete(HRChange).where(HRChange.company_id == company_id))
                        await cleanup.execute(delete(EmployeeSalaryRate).where(EmployeeSalaryRate.company_id == company_id))
            except Exception:
                await cleanup.rollback()
        await engine.dispose()
