"""Recipient lifecycle, real HTTP permissions and database history protections."""
import importlib.util
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from test_employee_foundation import employee_engine
from test_employment_structure import setup, contract
from app.api.deps import get_current_user
from app.core.database import get_db
from app.api.v1.payroll_deductions import router
from app.models.account import Account
from app.models.user import User
from app.models.permission import Permission
from app.models.role import Role
from app.models.rbac import role_permissions
from app.models.user_company_role import UserCompanyRole
from app.models.payroll_deduction import PayrollDeductionInstruction
from app.models.payroll_deduction_legal_evidence import PayrollDeductionLegalEvidence
from app.models.payroll_deduction_beneficiary import PayrollDeductionBeneficiary as Recipient
from app.schemas.payroll_deduction_beneficiary import DeductionBeneficiaryCreate
from app.services.payroll_deduction_beneficiary_service import (
    create_beneficiary, verify_beneficiary, revoke_beneficiary, get_beneficiary,
    DeductionBeneficiaryError, DeductionBeneficiaryNotFound,
)
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E') != '1', reason='Isolated PostgreSQL required')]
IBAN = 'UA903052992990004149123456789'


def migration():
    path = next(Path('alembic/versions').glob('13d4a7b8c029_*.py'))
    spec = importlib.util.spec_from_file_location('recipient029', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


async def protect(engine):
    async with engine.begin() as connection:
        def install(sync):
            import ast
            source = Path(migration().__file__).read_text()
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'execute' and node.args and isinstance(node.args[0], ast.Constant):
                    sql = node.args[0].value
                    if sql.lstrip().startswith(('CREATE FUNCTION','CREATE TRIGGER')):
                        sync.execute(text(sql))
        await connection.run_sync(install)


async def prepare(db):
    f = await setup(db)
    c = await contract(db, f)
    role = Role(name='recipient-manager')
    permissions = [Permission(name=name) for name in ('employees.manage', 'employees.read')]
    db.add_all([role, *permissions]); await db.flush()
    await db.execute(insert(role_permissions), [dict(role_id=role.id, permission_id=p.id) for p in permissions])
    db.add_all([UserCompanyRole(user_id=uid, company_id=f['company'], role_id=role.id, is_active=True)
                for uid in (f['actor'], f['linked_user'])])
    account = Account(company_id=f['company'], code='6851', name='Recipients', account_type='liability',
        normal_balance='credit', is_active=True, is_postable=True)
    instruction = PayrollDeductionInstruction(company_id=f['company'], employment_contract_id=c.id,
        deduction_type='enforcement', method='fixed', fixed_amount=Decimal('1000'), priority=1,
        currency_code='UAH', effective_from=date(2026,9,1), request_key='recipient-test',
        created_by=f['actor'], created_at=datetime.now(timezone.utc))
    db.add_all([account, instruction]); await db.flush()
    evidence = PayrollDeductionLegalEvidence(company_id=f['company'], payroll_deduction_instruction_id=instruction.id,
        legal_category='enforcement_other', document_reference='Verified order', verification_status='verified',
        verified_by=f['linked_user'], verified_at=datetime.now(timezone.utc), created_by=f['actor'],
        created_at=datetime.now(timezone.utc))
    db.add(evidence); await db.flush()
    data = DeductionBeneficiaryCreate(request_key='recipient-1', recipient_name='Executor',
        recipient_identifier='TEST-ID', iban=IBAN, settlement_kind='recipient_payment', account_id=account.id,
        document_reference='Verified bank details in order', valid_from=date(2026,9,1))
    return f, instruction, evidence, data


async def test_lifecycle_retries_permissions_and_raw_sql_protection(employee_engine):
    await protect(employee_engine)
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f, instruction, evidence, data = await prepare(db)
        args = dict(company_id=f['company'], instruction_id=instruction.id, data=data, actor_id=f['actor'])
        row = await create_beneficiary(db, **args)
        assert (await create_beneficiary(db, **args)).id == row.id
        with pytest.raises(DeductionBeneficiaryError, match='different recipient'):
            await create_beneficiary(db, **dict(args, data=data.model_copy(update={'recipient_name':'Other'})))
        with pytest.raises(DeductionBeneficiaryError, match='Permission denied'):
            await verify_beneficiary(db, company_id=f['company'], beneficiary_id=row.id, actor_id=f['foreign_user'])
        with pytest.raises(DeductionBeneficiaryError, match='independent'):
            await verify_beneficiary(db, company_id=f['company'], beneficiary_id=row.id, actor_id=f['actor'])
        with pytest.raises(DeductionBeneficiaryNotFound):
            await get_beneficiary(db, company_id=f['other_company'], beneficiary_id=row.id)
        await verify_beneficiary(db, company_id=f['company'], beneficiary_id=row.id, actor_id=f['linked_user'])
        assert row.status == 'verified'
        await db.commit()
        for statement in (
            'UPDATE payroll_deduction_beneficiaries SET recipient_name=\'Changed\' WHERE id=:id',
            'UPDATE payroll_deduction_beneficiaries SET verified_by=created_by WHERE id=:id',
            'DELETE FROM payroll_deduction_beneficiaries WHERE id=:id',
            'TRUNCATE payroll_deduction_beneficiaries CASCADE',
        ):
            with pytest.raises(DBAPIError):
                async with db.begin_nested():
                    await db.execute(text(statement), {'id':row.id})
        other = await create_beneficiary(db, **dict(args, data=data.model_copy(update={'request_key':'recipient-2'})))
        with pytest.raises(DeductionBeneficiaryError, match='overlaps'):
            await verify_beneficiary(db, company_id=f['company'], beneficiary_id=other.id, actor_id=f['linked_user'])
        await revoke_beneficiary(db, company_id=f['company'], beneficiary_id=row.id, actor_id=f['actor'])
        await verify_beneficiary(db, company_id=f['company'], beneficiary_id=other.id, actor_id=f['linked_user'])
        with pytest.raises(DeductionBeneficiaryError, match='Revoked'):
            await verify_beneficiary(db, company_id=f['company'], beneficiary_id=row.id, actor_id=f['linked_user'])
        with pytest.raises(RuntimeError, match='history'):
            async with db.begin_nested():
                def downgrade(sync):
                    with Operations.context(MigrationContext.configure(sync)):
                        migration().downgrade()
                await (await db.connection()).run_sync(downgrade)


async def test_http_validation_auth_and_independent_verification(employee_engine):
    await protect(employee_engine)
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f, instruction, evidence, data = await prepare(db)
        await db.commit()
        app = FastAPI(); app.include_router(router)
        actor = await db.get(User, f['actor'])
        async def current(): return actor
        async def connection(): yield db
        app.dependency_overrides[get_db] = connection
        app.dependency_overrides[get_current_user] = current
        root = f"/companies/{f['company']}"
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            url = root + f'/payroll-deductions/{instruction.id}/beneficiaries'
            bad = await client.post(url, json=dict(data.model_dump(mode='json'), iban='UA000000'))
            assert bad.status_code == 422
            result = await client.post(url, json=data.model_dump(mode='json'))
            assert result.status_code == 201, result.text
            bid = result.json()['id']
            assert result.json()['status'] == 'pending'
            verify = root + f'/payroll-deduction-beneficiaries/{bid}/verify'
            assert (await client.post(verify)).status_code == 409
            actor = await db.get(User, f['foreign_user'])
            assert (await client.post(verify)).status_code == 403
            actor = await db.get(User, f['linked_user'])
            assert (await client.post(verify)).json()['status'] == 'verified'
            assert (await client.post(verify)).status_code == 200
            assert (await client.post(root + f'/payroll-deduction-beneficiaries/{bid}/revoke')).json()['status'] == 'revoked'


async def test_execution_resolution_rechecks_document_and_account(employee_engine):
    from app.services.payroll_deduction_beneficiary_service import resolve_verified_beneficiary
    await protect(employee_engine)
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f, instruction, evidence, data = await prepare(db)
        row = await create_beneficiary(db, company_id=f['company'], instruction_id=instruction.id, data=data, actor_id=f['actor'])
        args = dict(company_id=f['company'], instruction_id=instruction.id, payment_date=date(2026,9,20))
        with pytest.raises(DeductionBeneficiaryError, match='Exactly one'):
            await resolve_verified_beneficiary(db, **args)
        await verify_beneficiary(db, company_id=f['company'], beneficiary_id=row.id, actor_id=f['linked_user'])
        assert (await resolve_verified_beneficiary(db, **args)).id == row.id
        evidence.verification_status = 'rejected'; await db.flush()
        with pytest.raises(DeductionBeneficiaryError, match='Verified deduction document'):
            await resolve_verified_beneficiary(db, **args)
        evidence.verification_status = 'verified'
        account = await db.get(Account, row.account_id)
        account.is_active = False; await db.flush()
        with pytest.raises(DeductionBeneficiaryError, match='Active company'):
            await resolve_verified_beneficiary(db, **args)
        account.is_active = True; await db.flush()
        with pytest.raises(DeductionBeneficiaryError, match='Exactly one'):
            await resolve_verified_beneficiary(db, **dict(args, payment_date=date(2026,8,31)))


async def test_cli_migration_roundtrip_preserves_existing_tables():
    import asyncio
    import sys
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.engine import make_url
    url = os.environ['DATABASE_URL']
    assert make_url(url).database.startswith('erp_r3_copy_')
    engine = create_async_engine(url)
    async def run(*args):
        proc = await asyncio.create_subprocess_exec(sys.executable, '-m', 'alembic', *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        output, _ = await proc.communicate()
        assert proc.returncode == 0, output.decode()
    async def snapshot():
        async with engine.connect() as conn:
            tables = (await conn.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename NOT IN ('alembic_version','payroll_deduction_beneficiaries','payroll_deduction_assessments','payroll_withholding_batches','payroll_withholding_lines','payroll_recipient_remittances','payroll_recipient_remittance_lines','payroll_deduction_assessment_closures') ORDER BY tablename"))).all()
            values = {}
            for name in tables:
                quoted = conn.dialect.identifier_preparer.quote(name)
                exclusions = "ARRAY['withheld_amount','source_withholding_line_id','payment_beneficiary_id']" + (" || ARRAY['revision']" if name == "payroll_payslips" else "")
                values[name] = tuple((await conn.execute(text(f"SELECT count(*), md5(COALESCE(string_agg((to_jsonb(t) - ({exclusions}))::text, E'\\n' ORDER BY (to_jsonb(t) - ({exclusions}))::text),'')) FROM public.{quoted} t"))).one())
            return values
    try:
        before = await snapshot()
        await run('upgrade', 'head')
        await run('check')
        assert await snapshot() == before
        await run('downgrade', '13d4a7b8c028')
        assert await snapshot() == before
        await run('upgrade', 'head')
        await run('check')
        assert await snapshot() == before
    finally:
        await engine.dispose()


async def test_concurrent_verification_selects_one_recipient(employee_engine):
    import asyncio
    await protect(employee_engine)
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f, instruction, evidence, data = await prepare(db)
        first = await create_beneficiary(db, company_id=f['company'], instruction_id=instruction.id,
            data=data, actor_id=f['actor'])
        second = await create_beneficiary(db, company_id=f['company'], instruction_id=instruction.id,
            data=data.model_copy(update={'request_key':'second'}), actor_id=f['actor'])
        ids = first.id, second.id
        await db.commit()
    async def verify(bid):
        async with AsyncSession(employee_engine) as db:
            try:
                await verify_beneficiary(db, company_id=f['company'], beneficiary_id=bid, actor_id=f['linked_user'])
                await db.commit()
                return 'verified'
            except DeductionBeneficiaryError:
                await db.rollback()
                return 'conflict'
    assert sorted(await asyncio.gather(*(verify(bid) for bid in ids))) == ['conflict', 'verified']


async def test_employer_recovery_is_not_an_external_bank_recipient(employee_engine):
    await protect(employee_engine)
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f, instruction, evidence, data = await prepare(db)
        asset = Account(company_id=f['company'], code='3771', name='Employee receivable', account_type='asset',
            normal_balance='debit', is_active=True, is_postable=True)
        db.add(asset); await db.flush()
        recovery = DeductionBeneficiaryCreate.model_validate(dict(data.model_dump(),
            settlement_kind='employer_recovery', account_id=asset.id, iban=None))
        row = await create_beneficiary(db, company_id=f['company'], instruction_id=instruction.id,
            data=recovery, actor_id=f['actor'])
        with pytest.raises(DeductionBeneficiaryError, match='legal category'):
            await verify_beneficiary(db, company_id=f['company'], beneficiary_id=row.id, actor_id=f['linked_user'])
        evidence.legal_category = 'employer_statutory'; await db.flush()
        await verify_beneficiary(db, company_id=f['company'], beneficiary_id=row.id, actor_id=f['linked_user'])
        assert row.iban is None and row.status == 'verified'
