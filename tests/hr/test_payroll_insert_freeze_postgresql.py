"""Actual Alembic round trips and two-session insertion/sealing races.

Run only through scripts/run_postgresql_regression_copy.py.
"""
import asyncio
import os
import subprocess
import sys
from datetime import date, datetime, UTC
from decimal import Decimal

import pytest
from sqlalchemy import text, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.core.config import settings
from app.models.payroll import (
    PayrollPeriod, PayrollInput, PayrollInputSalarySlice,
    PayrollCalculation, PayrollCalculationLine,
)
from app.models.employee_salary_rate import EmployeeSalaryRate
from app.services.payroll_calculation_service import calculate_payroll_input
from tests.hr.test_payroll_correction_postgresql import _seed_identity, _required_values


def isolated_url():
    url = make_url(settings.database_url)
    assert os.environ.get('RUN_POSTGRES_E2E') == '1'
    assert url.get_backend_name() == 'postgresql'
    assert (url.database or '').startswith('erp_r3_copy_')
    return url


def migrate(*args):
    isolated_url()
    result = subprocess.run(
        [sys.executable, '-m', 'alembic', *args],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(scope='module', autouse=True)
def migrated():
    migrate('upgrade', '13d4a7b8c027')
    try:
        yield
    finally:
        migrate('downgrade', '13d4a7b8c025')


async def seed(db):
    company, actor, employee, contract = await _seed_identity(db)
    period = PayrollPeriod(**_required_values(PayrollPeriod, dict(
        company_id=company.id, year=2026, month=4,
        start_date=date(2026, 4, 1), end_date=date(2026, 4, 30),
        status='finalized', finalized_at=datetime.now(UTC),
        finalized_by=actor.id, created_by=actor.id,
    )))
    db.add(period)
    await db.flush()
    rate = EmployeeSalaryRate(
        company_id=company.id, employment_contract_id=contract.id,
        rate_type='hourly', amount=Decimal('40'), currency_code='UAH',
        effective_from=period.start_date, effective_to=period.end_date,
        created_by=actor.id,
    )
    db.add(rate)
    await db.flush()
    inp = PayrollInput(**_required_values(PayrollInput, dict(
        company_id=company.id, payroll_period_id=period.id,
        employment_contract_id=contract.id, created_by=actor.id,
        scheduled_minutes=6000, worked_minutes=6000,
    )))
    db.add(inp)
    await db.flush()
    salary = PayrollInputSalarySlice(
        company_id=company.id, payroll_input_id=inp.id, salary_rate_id=rate.id,
        salary_rate_type='hourly', salary_rate_amount=Decimal('40'),
        currency_code='UAH', effective_from=period.start_date,
        effective_to=period.end_date, scheduled_minutes=6000, worked_minutes=6000,
    )
    db.add(salary)
    await db.flush()
    return dict(company_id=company.id, payroll_input_id=inp.id, calculated_by=actor.id), salary.id


SLICE_INSERT = """INSERT INTO payroll_input_salary_slices
(company_id,payroll_input_id,salary_rate_id,salary_rate_type,salary_rate_amount,
currency_code,effective_from,effective_to,scheduled_minutes,worked_minutes,created_at)
SELECT company_id,payroll_input_id,salary_rate_id,salary_rate_type,salary_rate_amount,
currency_code,effective_from,effective_to,0,0,created_at
FROM payroll_input_salary_slices WHERE id=:id RETURNING id"""
LINE_INSERT = """INSERT INTO payroll_calculation_lines
(company_id,payroll_calculation_id,line_no,line_type,description,quantity,rate,amount,currency_code)
SELECT company_id,id,999,'salary','late line',0,0,0,currency_code
FROM payroll_calculations WHERE id=:id RETURNING id"""


@pytest.mark.asyncio
async def test_initial_calculation_late_inserts_and_rollback():
    engine = create_async_engine(isolated_url())
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as db:
            args, salary_id = await seed(db)
            calc = await calculate_payroll_input(db, **args)
            assert calc.gross_amount == Decimal('5200.00')
            assert len((await db.scalars(select(PayrollCalculationLine).where(
                PayrollCalculationLine.payroll_calculation_id == calc.id))).all()) == 2
            assert db.in_transaction()  # service did not take transaction ownership
            for sql, row_id, message in [
                (SLICE_INSERT, salary_id, 'membership is immutable'),
                ('UPDATE payroll_inputs SET history_sealed=false WHERE id=:id',
                 args['payroll_input_id'], 'Historical payroll source is immutable'),
            ]:
                with pytest.raises(Exception, match=message):
                    async with db.begin_nested():
                        await db.execute(text(sql), {'id': row_id})
            await db.commit()
            with pytest.raises(Exception, match='lines are sealed'):
                async with db.begin_nested():
                    await db.execute(text(LINE_INSERT), {'id': calc.id})
            assert await db.scalar(text('SELECT gross_amount FROM payroll_calculations WHERE id=:id'), {'id': calc.id}) == Decimal('5200')
            args2, _ = await seed(db)
            rolled_back = await calculate_payroll_input(db, **args2)
            calc_id = rolled_back.id
            await db.rollback()
            assert await db.scalar(text('SELECT count(*) FROM payroll_calculations WHERE id=:id'), {'id': calc_id}) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('isolation', ['READ COMMITTED', 'REPEATABLE READ', 'SERIALIZABLE'])
@pytest.mark.parametrize('winner', ['calculation', 'slice'])
async def test_slice_calculation_concurrency(isolation, winner):
    engine = create_async_engine(isolated_url(), isolation_level=isolation)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    task = None
    try:
        async with Session() as setup:
            args, salary_id = await seed(setup)
            await setup.commit()
        async with Session() as first, Session() as second:
            # Establish the losing transaction's snapshot before the winner writes.
            await second.execute(text('SELECT count(*) FROM payroll_inputs'))
            second_pid = await second.scalar(text('SELECT pg_backend_pid()'))
            await second.execute(text("SET LOCAL statement_timeout='8s'"))
            if winner == 'calculation':
                await calculate_payroll_input(first, **args)
                operation = second.execute(text(SLICE_INSERT), {'id': salary_id})
            else:
                await first.execute(text(SLICE_INSERT), {'id': salary_id})
                operation = calculate_payroll_input(second, **args)
            task = asyncio.create_task(operation)
            # Prove a real database lock wait, not merely scheduler timing.
            observer = create_async_engine(isolated_url())
            try:
                async with observer.connect() as conn:
                    for _ in range(100):
                        blocked = await conn.scalar(text(
                            'SELECT cardinality(pg_blocking_pids(:pid)) > 0'
                        ), {'pid': second_pid})
                        if blocked:
                            break
                        if task.done():
                            await task
                            pytest.fail('Competing writer did not wait for shared input lock')
                        await asyncio.sleep(.02)
                    assert blocked
            finally:
                await observer.dispose()
            await first.commit()
            if winner == 'slice' and isolation == 'READ COMMITTED':
                calculation = await asyncio.wait_for(task, 10)
                assert calculation.gross_amount == Decimal('5200')
                await second.commit()
            else:
                pattern = ('membership is immutable' if isolation == 'READ COMMITTED'
                           else 'could not serialize access')
                with pytest.raises(Exception, match=pattern):
                    await asyncio.wait_for(task, 10)
                await second.rollback()
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()


@pytest.mark.asyncio
async def test_actual_alembic_roundtrip_and_legacy_seal():
    engine = create_async_engine(isolated_url())
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        migrate('downgrade', '13d4a7b8c025')
        async with Session() as db:
            args, salary_id = await seed(db)
            calc = await calculate_payroll_input(db, **args)
            calc_id = calc.id
            await db.commit()
        migrate('upgrade', '13d4a7b8c027')
        async with Session() as db:
            assert await db.scalar(text('SELECT version_num FROM alembic_version')) == '13d4a7b8c027'
            assert await db.scalar(text('SELECT history_creation_xid FROM payroll_calculations WHERE id=:id'), {'id': calc_id}) is None
            for sql, row_id, pattern in [
                (SLICE_INSERT, salary_id, 'membership is immutable'),
                (LINE_INSERT, calc_id, 'lines are sealed'),
            ]:
                with pytest.raises(Exception, match=pattern):
                    async with db.begin_nested():
                        await db.execute(text(sql), {'id': row_id})
            assert await db.scalar(text('SELECT gross_amount FROM payroll_calculations WHERE id=:id'), {'id': calc_id}) == Decimal('5200')
            await db.rollback()
        migrate('downgrade', '13d4a7b8c026')
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT count(*) FROM information_schema.columns WHERE table_name='payroll_inputs' AND column_name='history_sealed'")) == 0
            assert await conn.scalar(text("SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'trg_%_history_freeze'")) == 9
        migrate('upgrade', '13d4a7b8c027')
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_freeze_does_not_authorize_unproven_revision_two():
    from app.services.payroll_correction_source_evidence_service import capture_payroll_correction_source
    from app.services.payroll_correction_recalculation_service import recalculate_minimum_wage_correction
    from app.services.payroll_correction_recalculation_contract import PayrollCorrectionRecalculationError
    engine = create_async_engine(isolated_url())
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as db:
            args, _ = await seed(db)
            original = await calculate_payroll_input(db, **args)
            common = dict(company_id=args['company_id'],
                          original_payroll_calculation_id=original.id,
                          request_key=f'sealed-{original.id}', reason='Documented correction')
            evidence = await capture_payroll_correction_source(db, **common)
            assert evidence['source_evidence_complete'] is False
            assert evidence['financial_execution_allowed'] is False
            with pytest.raises(PayrollCorrectionRecalculationError, match='evidence is incomplete'):
                await recalculate_minimum_wage_correction(
                    db, **common, calculated_by=args['calculated_by'],
                    corrected_minimum_wage_context=dict(full_time_norm_minutes=10000,
                                                       norm_reference='Document E3-001'),
                    expected_source_fingerprint_sha256=evidence['source_fingerprint_sha256'],
                )
            assert await db.scalar(text('SELECT count(*) FROM payroll_calculations WHERE payroll_input_id=:id'), {'id': args['payroll_input_id']}) == 1
            assert await db.scalar(text('SELECT count(*) FROM payroll_corrections WHERE company_id=:id'), {'id': args['company_id']}) == 0
            assert db.in_transaction()
            await db.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unreferenced_slice_edits_and_move_into_sealed_input():
    engine = create_async_engine(isolated_url())
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as db:
            args, salary_id = await seed(db)
            other_args, other_id = await seed(db)
            await db.execute(text('UPDATE payroll_input_salary_slices SET worked_minutes=worked_minutes WHERE id=:id'), {'id': salary_id})
            extra = await db.scalar(text(SLICE_INSERT), {'id': salary_id})
            await db.execute(text('DELETE FROM payroll_input_salary_slices WHERE id=:id'), {'id': extra})
            await calculate_payroll_input(db, **args)
            with pytest.raises(Exception, match='membership is immutable'):
                async with db.begin_nested():
                    await db.execute(text('UPDATE payroll_input_salary_slices SET payroll_input_id=:parent, company_id=:company WHERE id=:id'),
                                     dict(parent=args['payroll_input_id'], company=args['company_id'], id=other_id))
            assert await db.scalar(text('SELECT payroll_input_id FROM payroll_input_salary_slices WHERE id=:id'), {'id': other_id}) == other_args['payroll_input_id']
            await db.rollback()
    finally:
        await engine.dispose()
