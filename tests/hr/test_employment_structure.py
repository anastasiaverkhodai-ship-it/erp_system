"""13.2 directory, API, access, audit and concurrency acceptance tests."""
import asyncio
import importlib.util
import os
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from test_employee_foundation import employee_engine, seed, create
from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.models.company import Company
from app.models.employee import Employee, EmployeeStatus
from app.models.hr_change import HRChange
from app.models.employment_contract import EmploymentContract, EmploymentContractStatus, WorkArrangement
from app.models.department import Department
from app.models.position import Position
from app.models.permission import Permission
from app.models.role import Role
from app.models.rbac import role_permissions
from app.models.user_company_role import UserCompanyRole
from app.services.employee_service import update_employee, EmployeeLifecycleError
from app.services.employment_structure_service import (
    create_department, update_department, create_position, update_position,
    create_employment_contract, update_employment_contract,
    EmploymentStructureLifecycleError, EmploymentStructureDuplicateError,
    EmploymentStructureNotFoundError,
)
from app.services.hr_change_service import list_changes
from app.services.employee_bank_details import normalize_employee_iban

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E') != '1', reason='Set RUN_POSTGRES_E2E=1')]


async def setup(db):
    f = await seed(db)
    e = await create(db, f)
    d = await create_department(db, company_id=f['company'], code='OPS', name='Operations', parent_id=None, created_by=f['actor'])
    p = await create_position(db, company_id=f['company'], code='ENG', name='Engineer', created_by=f['actor'])
    f.update(employee=e.id, department=d.id, position=p.id)
    await db.commit()
    return f


async def contract(db, f, number='C-1', **extra):
    data = dict(company_id=f['company'], employee_id=f['employee'], contract_number=number,
        contract_type='employment', department_id=f['department'], position_id=f['position'],
        work_arrangement=WorkArrangement.FULL_TIME, start_date=date(2026,1,10), end_date=None,
        status=EmploymentContractStatus.ACTIVE, created_by=f['actor'])
    data.update(extra)
    return await create_employment_contract(db, **data)


async def test_department_cycles_and_reparenting(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f = await setup(db)
        b = await create_department(db, company_id=f['company'], code='B', name='B', parent_id=f['department'], created_by=f['actor'])
        c = await create_department(db, company_id=f['company'], code='C', name='C', parent_id=b.id, created_by=f['actor'])
        for parent in (f['department'], b.id, c.id):
            with pytest.raises(EmploymentStructureLifecycleError):
                async with db.begin_nested():
                    await update_department(db, company_id=f['company'], department_id=f['department'], parent_id=parent, fields_set={'parent_id'}, changed_by=f['actor'])
        await update_department(db, company_id=f['company'], department_id=c.id, parent_id=None, fields_set={'parent_id'}, changed_by=f['actor'])
        assert (await db.get(Department,c.id)).parent_id is None


async def test_concurrent_reparenting_cannot_create_cycle(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f = await setup(db)
        b = await create_department(db, company_id=f['company'], code='B', name='B', parent_id=None, created_by=f['actor'])
        bid = b.id
        await db.commit()
    async def move(child, parent):
        async with AsyncSession(employee_engine) as db:
            try:
                await update_department(db, company_id=f['company'], department_id=child, parent_id=parent, fields_set={'parent_id'}, changed_by=f['actor'])
                await db.commit()
                return 'ok'
            except EmploymentStructureLifecycleError:
                await db.rollback()
                return 'rejected'
    result = await asyncio.wait_for(asyncio.gather(move(f['department'],bid),move(bid,f['department'])),20)
    assert sorted(result) == ['ok','rejected']


async def test_concurrent_overlapping_contracts_are_serialized(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f = await setup(db)
    async def make(number):
        async with AsyncSession(employee_engine) as db:
            try:
                await contract(db,f,number)
                await db.commit()
                return 'ok'
            except EmploymentStructureDuplicateError:
                await db.rollback()
                return 'rejected'
    results = await asyncio.wait_for(asyncio.gather(make('A'),make('B')),20)
    assert sorted(results) == ['ok','rejected']


async def test_contract_boundaries_cancellation_and_directory_usage(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f = await setup(db)
        c = await contract(db,f)
        for updater, key, value in [(update_department,'department_id',f['department']),(update_position,'position_id',f['position'])]:
            with pytest.raises(EmploymentStructureLifecycleError):
                async with db.begin_nested():
                    await updater(db, company_id=f['company'], **{key:value}, is_active=False, fields_set={'is_active'})
        with pytest.raises(EmployeeLifecycleError):
            async with db.begin_nested():
                await update_employee(db, company_id=f['company'], employee_id=f['employee'],status=EmployeeStatus.TERMINATED,
                    termination_date=date(2026,2,1),fields_set={'status','termination_date'})
        await update_employment_contract(db, company_id=f['company'],contract_id=c.id,
            end_date=date(2026,1,31),status=EmploymentContractStatus.ENDED,fields_set={'end_date','status'},changed_by=f['actor'])
        with pytest.raises(EmploymentStructureDuplicateError):
            async with db.begin_nested():
                await contract(db,f,'same-day',start_date=date(2026,1,31))
        next_contract = await contract(db,f,'next',start_date=date(2026,2,1))
        await update_employment_contract(db,company_id=f['company'],contract_id=next_contract.id,
            status=EmploymentContractStatus.CANCELLED,fields_set={'status'},changed_by=f['actor'])
        await update_position(db,company_id=f['company'],position_id=f['position'],is_active=False,fields_set={'is_active'})
        with pytest.raises(EmploymentStructureNotFoundError):
            async with db.begin_nested():
                await update_employment_contract(db,company_id=f['company'],contract_id=next_contract.id,
                    status=EmploymentContractStatus.ACTIVE,fields_set={'status'})
        history = await list_changes(db,f['company'],'employment_contract',c.id)
        assert len(history)==2
        assert history[1].before_state['status']=='active'
        assert history[1].after_state['status']=='ended'
        assert history[1].changed_by==f['actor']


async def test_company_isolation_inactive_directory_and_employee_dates(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f=await setup(db)
        d=await create_department(db,company_id=f['other_company'],code='X',name='Foreign',parent_id=None,created_by=f['actor'])
        with pytest.raises(EmploymentStructureNotFoundError):
            async with db.begin_nested():
                await contract(db,f,department_id=d.id)
        with pytest.raises(EmploymentStructureLifecycleError):
            async with db.begin_nested():
                await contract(db,f,start_date=date(2026,1,1))
        await update_position(db,company_id=f['company'],position_id=f['position'],is_active=False,fields_set={'is_active'})
        with pytest.raises(EmploymentStructureNotFoundError):
            async with db.begin_nested():
                await contract(db,f)
        company=await db.get(Company,f['company']);company.is_active=False
        await db.flush()
        with pytest.raises(EmploymentStructureNotFoundError):
            await update_department(db,company_id=f['company'],department_id=f['department'],name='No',fields_set={'name'})


async def test_payment_details_checksum_normalization_and_audit(employee_engine):
    iban='UA903052992990004149123456789'
    assert normalize_employee_iban('ua90 305299 2990004149123456789')==iban
    for invalid in ('UA003052992990004149123456789','UA903052992990004149123456780','DE89370400440532013000','UA９０3052992990004149123456789'):
        with pytest.raises(ValueError): normalize_employee_iban(invalid)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f=await setup(db)
        await update_employee(db,company_id=f['company'],employee_id=f['employee'],payment_iban=iban,fields_set={'payment_iban'},changed_by=f['actor'])
        await update_employee(db,company_id=f['company'],employee_id=f['employee'],payment_iban=None,fields_set={'payment_iban'},changed_by=f['actor'])
        history=await list_changes(db,f['company'],'employee',f['employee'])
        assert [h.after_state['payment_iban'] for h in history]==[None,iban,None]
        assert history[-1].before_state['payment_iban']==iban


async def test_http_access_validation_audit_and_rollback(employee_engine):
    from app.api.v1.employees import router as employees
    from app.api.v1.employment_structure import router as structure
    from app.api.v1.hr_history import router as history
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f=await setup(db)
        role=Role(name='test HR');read=Permission(name='employees.read');manage=Permission(name='employees.manage')
        db.add_all([role,read,manage]);await db.flush()
        await db.execute(role_permissions.insert(),[dict(role_id=role.id,permission_id=p.id) for p in (read,manage)])
        db.add(UserCompanyRole(user_id=f['actor'],company_id=f['company'],role_id=role.id))
        manage_id=manage.id
        await db.commit()
    api=FastAPI()
    for r in (employees,structure,history):api.include_router(r)
    async def database():
        async with AsyncSession(employee_engine,expire_on_commit=False) as db:yield db
    api.dependency_overrides[get_db]=database
    api.dependency_overrides[get_current_user]=lambda:User(id=f['actor'])
    base=f"/companies/{f['company']}"
    async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as client:
        for resource in ('employees','departments','positions','employment-contracts'):
            assert (await client.get(base+'/'+resource)).status_code==200
            assert (await client.get(f"/companies/{f['other_company']}/"+resource)).status_code==403
        dep=base+f"/departments/{f['department']}"
        assert (await client.patch(dep,json={'name':'Operations 2'})).status_code==200
        result=await client.get(dep+'/history')
        assert result.status_code==200 and len(result.json())==2
        assert result.json()[-1]['changed_by']==f['actor']
        assert (await client.patch(dep,json={'name':'Corrupt','parent_id':f['department']})).status_code==422
        result=await client.get(dep+'/history')
        assert len(result.json())==2
        assert (await client.get(base+'/departments')).json()[0]['name']=='Operations 2'
        assert (await client.post(base+'/positions',json={'code':'X','name':'X','unexpected':1})).status_code==422
        assert (await client.patch(base+f"/employees/{f['employee']}",json={'payment_iban':'invalid'})).status_code==422
        body={'employee_id':f['employee'],'contract_number':'HTTP','contract_type':'employment','start_date':'2026-01-10'}
        result=await client.post(base+'/employment-contracts',json=body)
        assert result.status_code==201,result.text
        cid=result.json()['id']
        result=await client.patch(base+f'/employment-contracts/{cid}',json={'contract_type':'updated'})
        assert result.status_code==200,result.text
        assert len((await client.get(base+f'/employment-contracts/{cid}/history')).json())==2
        async with AsyncSession(employee_engine) as db:
            await db.execute(role_permissions.delete().where(role_permissions.c.permission_id==manage_id));await db.commit()
        assert (await client.patch(dep,json={'name':'Forbidden'})).status_code==403
        assert (await client.get(dep+'/history')).status_code==200
        assert (await client.get(base+'/employees/999999/history')).status_code==404
        async with AsyncSession(employee_engine) as db:
            await db.execute(role_permissions.delete());await db.commit()
        assert (await client.get(dep+'/history')).status_code==403


def migration():
    path=Path('alembic/versions/13c2a7e9b001_complete_hr_directories.py')
    spec=importlib.util.spec_from_file_location('hr_closure_migration',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


async def test_migration_default_permissions_baseline_and_preservation(employee_engine):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    m=migration()
    async with employee_engine.begin() as conn:
        def down(sync):
            with Operations.context(MigrationContext.configure(sync)):m.downgrade()
        await conn.run_sync(down)
        await conn.execute(text("INSERT INTO roles(name) VALUES ('admin'),('accountant')"))
        # Genuine prior-schema record: migration must preserve data and record baseline.
        await conn.execute(insert(Company).values(name="Legacy HR",is_active=True))
        await conn.execute(insert(User).values(email="migration@example.test",password_hash="unused",first_name="Test",last_name="Actor",is_active=True))
        await conn.execute(text("INSERT INTO employees(company_id,employee_number,first_name,last_name,hire_date,created_by) SELECT c.id,'LEGACY','Test','Employee','2026-01-10',u.id FROM companies c CROSS JOIN users u"))
        before=await conn.scalar(text("SELECT (to_jsonb(e))::text FROM employees e"))
        def up(sync):
            with Operations.context(MigrationContext.configure(sync)):m.upgrade()
        await conn.run_sync(up)
        assert await conn.scalar(text("SELECT (to_jsonb(e)-'payment_iban')::text FROM employees e"))==before
        assert await conn.scalar(text("SELECT column_default FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='employment_contracts' AND column_name='status'"))=="'active'::character varying"
        assert await conn.scalar(text('SELECT count(*) FROM hr_changes WHERE changed_by IS NULL'))==1
        grants=(await conn.execute(text('SELECT r.name,p.name FROM roles r JOIN role_permissions rp ON rp.role_id=r.id JOIN permissions p ON p.id=rp.permission_id ORDER BY r.name,p.name'))).all()
        assert grants==[('admin','employees.manage'),('admin','employees.read'),('hr_manager','employees.manage'),('hr_manager','employees.read')]
        def refuse(sync):
            with Operations.context(MigrationContext.configure(sync)):
                with pytest.raises(RuntimeError,match='history'):m.downgrade()
        await conn.run_sync(refuse)
