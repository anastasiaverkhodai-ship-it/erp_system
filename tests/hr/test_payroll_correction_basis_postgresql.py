"""Positive correction replay with actual 026-028 PostgreSQL protections."""
import asyncio
import importlib.util
import os
from pathlib import Path
from decimal import Decimal

import pytest
from sqlalchemy import select, text, func
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from alembic.migration import MigrationContext
from alembic.operations import Operations
from test_employee_foundation import employee_engine
from test_payroll_minimum_wage_postgresql import inputs
from app.models.payroll import PayrollCalculation, PayrollCalculationLine
from app.models.payroll_correction import PayrollCorrection
from app.services.payroll_calculation_service import calculate_payroll_input
from app.services.payroll_correction_source_evidence_service import capture_payroll_correction_source
from app.services.payroll_correction_recalculation_service import recalculate_minimum_wage_correction
from app.services.payroll_correction_recalculation_contract import PayrollCorrectionRecalculationError

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E') != '1', reason='PostgreSQL required')]


def migration(number):
    path = next(Path('alembic/versions').glob(f'13d4a7b8c0{number}_*.py'))
    spec = importlib.util.spec_from_file_location('basis_migration'+number, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def protect(engine):
    async with engine.begin() as conn:
        def apply(sync):
            with Operations.context(MigrationContext.configure(sync)):
                for number in ('26', '27', '28'):
                    migration(number).upgrade()
        await conn.run_sync(apply)


async def prepare(db, *, adjustment=Decimal(0)):
    f, source = await inputs(db, part_time=True, adjustment=adjustment)
    context = dict(full_time_norm_minutes=10560, norm_reference='Calendar original')
    if adjustment:
        context.update(manual_adjustment_category='ordinary_salary', manual_adjustment_reference='Order A')
    original = await calculate_payroll_input(db, company_id=f['company'], payroll_input_id=source.id,
        calculated_by=f['actor'], minimum_wage_context=context)
    evidence = await capture_payroll_correction_source(db, company_id=f['company'],
        original_payroll_calculation_id=original.id, request_key='correction-1', reason='Calendar correction')
    args = dict(company_id=f['company'], original_payroll_calculation_id=original.id,
        calculated_by=f['actor'], request_key='correction-1', reason='Calendar correction',
        expected_source_fingerprint_sha256=evidence['source_fingerprint_sha256'],
        corrected_minimum_wage_context=dict(context, full_time_norm_minutes=9600, norm_reference='Calendar corrected'))
    return f, source, original, evidence, args


async def test_positive_revision_retry_and_rollback(employee_engine):
    await protect(employee_engine)
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f, source, original, evidence, args = await prepare(db)
        assert evidence['source_evidence_complete'] is True
        assert evidence['evidence_scope'] == 'minimum_wage_context_only'
        assert original.gross_amount == Decimal('4323.50')
        before = dict(original.minimum_wage_assessment)
        revision, link = await recalculate_minimum_wage_correction(db, **args)
        assert revision.revision == 2
        assert revision.gross_amount == Decimal('4755.85')
        assert original.minimum_wage_assessment == before
        assert revision.minimum_wage_assessment['correction_basis']['seal'] == 'creation-028-v1'
        again, same_link = await recalculate_minimum_wage_correction(db, **args)
        assert (again.id, same_link.id) == (revision.id, link.id)
        with pytest.raises(PayrollCorrectionRecalculationError, match='different correction'):
            await recalculate_minimum_wage_correction(db, **dict(args, expected_source_fingerprint_sha256='0'*64))
        await db.rollback()
        assert await db.scalar(select(func.count(PayrollCorrection.id))) == 0


async def test_seal_survives_commit_and_sources_cannot_change(employee_engine):
    await protect(employee_engine)
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f, source, original, evidence, args = await prepare(db)
        await db.commit()
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        revision, link = await recalculate_minimum_wage_correction(db, **args)
        await db.commit()
        for statement in (
            'UPDATE payroll_calculations SET minimum_wage_assessment=NULL WHERE id=:id',
            'DELETE FROM payroll_calculations WHERE id=:id',
            'TRUNCATE payroll_calculations CASCADE',
        ):
            with pytest.raises(DBAPIError):
                async with db.begin_nested():
                    await db.execute(text(statement), {'id': original.id})
        assert await db.scalar(select(func.count(PayrollCalculation.id))) == 2
        def downgrade(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration('28').downgrade()
        with pytest.raises(RuntimeError, match='sealed history'):
            async with db.begin_nested():
                await (await db.connection()).run_sync(downgrade)


async def test_concurrent_identical_correction_has_one_revision(employee_engine):
    await protect(employee_engine)
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        _, _, _, _, args = await prepare(db)
        await db.commit()
    async def execute():
        async with AsyncSession(employee_engine, expire_on_commit=False) as db:
            revision, link = await recalculate_minimum_wage_correction(db, **args)
            await db.commit()
            return revision.id, link.id
    results = await asyncio.gather(execute(), execute())
    assert results[0] == results[1]
    async with AsyncSession(employee_engine) as db:
        assert await db.scalar(select(func.count(PayrollCorrection.id))) == 1


async def test_legacy_not_attested_by_installing_guards(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        _, _, original, evidence, args = await prepare(db)
        assert evidence['source_evidence_complete'] is False
        await db.commit()
    await protect(employee_engine)
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        with pytest.raises(PayrollCorrectionRecalculationError, match='evidence is incomplete'):
            await recalculate_minimum_wage_correction(db, **args)
        assert await db.scalar(select(func.count(PayrollCorrection.id))) == 0


async def test_manual_classification_and_future_revision(employee_engine):
    await protect(employee_engine)
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        _, _, original, _, args = await prepare(db, adjustment=Decimal('500'))
        context = dict(args['corrected_minimum_wage_context'], manual_adjustment_category='holiday_bonus',
                       manual_adjustment_reference='Corrected order B')
        revision, _ = await recalculate_minimum_wage_correction(db, **dict(args, corrected_minimum_wage_context=context))
        assert revision.gross_amount == Decimal('5255.85')
        assert original.gross_amount == Decimal('4323.50')
        new_evidence = await capture_payroll_correction_source(db, company_id=args['company_id'],
            original_payroll_calculation_id=revision.id, request_key='correction-2', reason='Next correction')
        third, _ = await recalculate_minimum_wage_correction(db, **dict(args,
            original_payroll_calculation_id=revision.id, request_key='correction-2', reason='Next correction',
            expected_source_fingerprint_sha256=new_evidence['source_fingerprint_sha256']))
        assert third.revision == 3
        assert third.gross_amount == Decimal('4755.85')


async def test_failed_link_rolls_back_revision_even_if_caller_commits(employee_engine, monkeypatch):
    import app.services.payroll_correction_recalculation_service as service
    await protect(employee_engine)
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        _, _, _, _, args = await prepare(db)
        await db.commit()
        async def fail(*args, **kwargs):
            raise RuntimeError('simulated correction-link failure')
        monkeypatch.setattr(service, 'create_payroll_correction_link', fail)
        with pytest.raises(RuntimeError, match='simulated'):
            await service.recalculate_minimum_wage_correction(db, **args)
        await db.commit()
        assert await db.scalar(select(func.count(PayrollCalculation.id))) == 1
        assert await db.scalar(select(func.count(PayrollCorrection.id))) == 0


async def test_http_evidence_correction_and_conflict(employee_engine):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from app.api.v1.payroll_calculations import router
    from app.api.deps import get_current_user
    from app.core.database import get_db
    from types import SimpleNamespace
    await protect(employee_engine)
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f, _, original, _, args = await prepare(db)
        original_id = original.id
        await db.commit()
        app = FastAPI()
        app.include_router(router)
        async def database():
            yield db
        async def user():
            return SimpleNamespace(id=f['actor'])
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user] = user
        endpoints = [route for route in router.routes if route.path.endswith(('correction-evidence', 'minimum-wage-correction'))]
        for route in endpoints:
            permission = next(dep.call for dep in route.dependant.dependencies if dep.name == '_')
            app.dependency_overrides[permission] = user
        prefix = f"/companies/{f['company']}/payroll-calculations/{original_id}"
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            payload = dict(request_key=args['request_key'], reason=args['reason'])
            response = await client.post(prefix+'/correction-evidence', json=payload)
            assert response.status_code == 200, response.text
            payload.update(expected_source_fingerprint_sha256=response.json()['source_fingerprint_sha256'],
                           corrected_minimum_wage_context=args['corrected_minimum_wage_context'])
            response = await client.post(prefix+'/minimum-wage-correction', json=payload)
            assert response.status_code == 200, response.text
            assert Decimal(response.json()['gross_amount']) == Decimal('4755.85')
            retry = await client.post(prefix+'/minimum-wage-correction', json=payload)
            assert retry.status_code == 200 and retry.json()['id'] == response.json()['id']
            payload['expected_source_fingerprint_sha256'] = '0'*64
            conflict = await client.post(prefix+'/minimum-wage-correction', json=payload)
            assert conflict.status_code == 409, conflict.text
            assert await db.scalar(select(func.count(PayrollCalculation.id))) == 2
