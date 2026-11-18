"""Real correction/snapshot interleavings; disposable PostgreSQL only."""
import asyncio
import hashlib
import os
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_correction_basis_postgresql import prepare, protect
from test_payroll_correction_multirevision_postgresql import install_and_verify_snapshot_migration
from app.models.company import Company
from app.models.payroll import PayrollPeriod
from app.models.payroll_statutory import PayrollStatutoryComponent
from app.services.payroll_statutory_service import create_statutory_rate
from app.services.payroll_dated_employee_statutory import write_dated_employee_statutory_result
from app.services.payroll_correction_recalculation_service import recalculate_minimum_wage_correction
from app.services.payroll_correction_source_evidence_service import capture_payroll_correction_source
from app.services.payroll_regulatory_correction_projection import canonical_bytes
from app.services.payroll_regulatory_correction_snapshot_store import (
    persist_correction_snapshot, CorrectionSnapshotError,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_E2E") != "1", reason="Disposable PostgreSQL required")]


async def seed(engine, next_norm):
    await protect(engine)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        await install_and_verify_snapshot_migration(db)
        await db.execute(text("SELECT pg_advisory_xact_lock(130018, 1)"))
        start = await db.scalar(text("SELECT COALESCE(MAX(company_id), 0) + 1000000 "
            "FROM public.payroll_regulatory_correction_snapshots"))
        await db.execute(text("SELECT setval(pg_get_serial_sequence('companies', 'id'), :start, false)"),
                         {"start": start})
        f, _, original, _, args = await prepare(db)
        period = await db.get(PayrollPeriod, original.payroll_period_id)
        for component in PayrollStatutoryComponent:
            # Explicit component rates, independent of enum declaration order.
            rates = {'personal_income_tax': Decimal('.18'), 'military_levy': Decimal('.05'),
                     'unified_social_contribution': Decimal('.22')}
            await create_statutory_rate(db, company_id=f['company'], component=component,
                rate=rates[component.value], effective_from=period.start_date,
                effective_to=None, actor_user_id=f['actor'])
        await write_dated_employee_statutory_result(db, company_id=f['company'],
            payroll_calculation_id=original.id, actor_user_id=f['actor'])
        second, _ = await recalculate_minimum_wage_correction(db, **args)
        await write_dated_employee_statutory_result(db, company_id=f['company'],
            payroll_calculation_id=second.id, actor_user_id=f['actor'])
        next_args = dict(args, original_payroll_calculation_id=second.id,
            request_key='concurrent-v3', reason='Concurrent calendar correction',
            corrected_minimum_wage_context=dict(args['corrected_minimum_wage_context'],
                full_time_norm_minutes=next_norm, norm_reference='Concurrent calendar'))
        evidence = await capture_payroll_correction_source(db, company_id=f['company'],
            original_payroll_calculation_id=second.id,
            request_key=next_args['request_key'], reason=next_args['reason'])
        next_args['expected_source_fingerprint_sha256'] = evidence['source_fingerprint_sha256']
        await db.commit()
        return f, period.id, original.id, second.id, second.gross_amount, next_args


async def wait_blocked(engine, pid):
    """Prove actual server-side contention; elapsed sleep is not the evidence."""
    async def observe():
        async with AsyncSession(engine) as observer:
            while True:
                blockers = await observer.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': pid})
                if blockers:
                    return blockers
                await asyncio.sleep(.01)
    return await asyncio.wait_for(observe(), timeout=10)


@pytest.mark.parametrize('kind', ['d1', '4df'])
@pytest.mark.parametrize('next_norm', [8800, 11000])
@pytest.mark.parametrize('ordering', ['first_insert', 'writer_first', 'reader_first', 'incomplete_writer'])
async def test_snapshot_source_interleaving(employee_engine, kind, next_norm, ordering):
    f, period, original, second, second_gross, args = await seed(employee_engine, next_norm)
    scope = dict(company_id=f['company'], payroll_period_id=period, report_kind=kind)
    pids = asyncio.Queue()
    tasks = []

    async def snapshot():
        async with AsyncSession(employee_engine) as session:
            await session.execute(text("SET LOCAL statement_timeout = '15000'"))
            await pids.put(await session.scalar(text('SELECT pg_backend_pid()')))
            result = await persist_correction_snapshot(session, **scope)
            await session.commit()
            return result

    async def correction():
        async with AsyncSession(employee_engine) as session:
            await session.execute(text("SET LOCAL statement_timeout = '15000'"))
            await pids.put(await session.scalar(text('SELECT pg_backend_pid()')))
            third, _ = await recalculate_minimum_wage_correction(session, **args)
            await write_dated_employee_statutory_result(session, company_id=f['company'],
                payroll_calculation_id=third.id, actor_user_id=f['actor'])
            ident, gross = third.id, third.gross_amount
            await session.commit()
            return ident, gross

    query = text('SELECT id, projection, projection_sha256 FROM '
                 'public.payroll_regulatory_correction_snapshots WHERE company_id=:company ORDER BY id')
    third_id = None
    try:
        async with AsyncSession(employee_engine, expire_on_commit=False) as holder:
            if ordering == 'first_insert':
                await holder.scalar(select(Company.id).where(Company.id == f['company']).with_for_update())
                tasks = [asyncio.create_task(snapshot()) for _ in range(2)]
                for _ in tasks:
                    await wait_blocked(employee_engine, await asyncio.wait_for(pids.get(), 10))
                await holder.commit()
                results = await asyncio.wait_for(asyncio.gather(*tasks), 20)
                assert results[0]['id'] == results[1]['id']
                assert sorted(row['created'] for row in results) == [False, True]
                assert all(row['official_generation'] == 'BLOCKED' for row in results)
            elif ordering == 'reader_first':
                first = await persist_correction_snapshot(holder, **scope)
                assert first['created'] is True
                frozen = (await holder.execute(query, {'company': f['company']})).mappings().all()
                tasks = [asyncio.create_task(correction())]
                await wait_blocked(employee_engine, await asyncio.wait_for(pids.get(), 10))
                await holder.commit()
                third_id, third_gross = await asyncio.wait_for(tasks[0], 20)
                result = await snapshot()
                assert result['created'] is True
            else:
                third, _ = await recalculate_minimum_wage_correction(holder, **args)
                third_id, third_gross = third.id, third.gross_amount
                tasks = [asyncio.create_task(snapshot())]
                await wait_blocked(employee_engine, await asyncio.wait_for(pids.get(), 10))
                if ordering == 'writer_first':
                    await write_dated_employee_statutory_result(holder, company_id=f['company'],
                        payroll_calculation_id=third_id, actor_user_id=f['actor'])
                await holder.commit()
                if ordering == 'incomplete_writer':
                    with pytest.raises(CorrectionSnapshotError, match='not verified'):
                        await asyncio.wait_for(tasks[0], 20)
                else:
                    result = await asyncio.wait_for(tasks[0], 20)
                    assert result['created'] is True
        async with AsyncSession(employee_engine) as reader:
            rows = (await reader.execute(query, {'company': f['company']})).mappings().all()
            assert len(rows) == {'first_insert': 1, 'reader_first': 2,
                                 'writer_first': 1, 'incomplete_writer': 0}[ordering]
            for row in rows:
                payload = row['projection']
                assert hashlib.sha256(canonical_bytes(payload)).hexdigest() == row['projection_sha256']
                assert payload['official_generation'] == 'BLOCKED'
                assert payload['source_provenance'] == 'PERSISTED_POSTGRESQL'
            if ordering == 'reader_first':
                assert rows[:1] == frozen
            if ordering in ('reader_first', 'writer_first'):
                payload = rows[-1]['projection']
                assert payload['baseline_calculation_ids'] == [second]
                assert payload['active_calculation_ids'] == [third_id]
                assert payload['excluded_historical_calculation_ids'] == [original, second]
                pit = next(c for c in payload['components'] if c['component'] == 'personal_income_tax')
                assert Decimal(pit['base_delta']) == third_gross - second_gross
                assert (Decimal(pit['base_delta']) > 0) == (next_norm == 8800)
            if ordering == 'first_insert':
                assert rows[0]['projection']['active_calculation_ids'] == [second]
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
