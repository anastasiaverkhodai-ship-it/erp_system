"""Real PostgreSQL absence evidence; never statutory non-applicability."""
from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import update, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_d5_period_coverage_postgresql import setup_period
from test_payroll_d5_event_sources_postgresql import _event
from app.models.payroll import PayrollPeriod
from app.models.employment_event import EmploymentEvent
from app.services.payroll_d5_database_field_projection import _canonical_digest
from app.services.payroll_d5_period_coverage import (
    D5PeriodCoverageError, build_d5_period_coverage_review,
    verify_d5_period_coverage_review,
)

pytestmark = pytest.mark.asyncio


async def empty_case(db):
    company, actor, contract, event, period, targets = await setup_period(db)
    period.start_date = event.effective_date + timedelta(days=40)
    period.end_date = period.start_date + timedelta(days=20)
    await db.flush()
    kwargs = dict(company_id=company.id, payroll_period_id=period.id, targets=[])
    review = await build_d5_period_coverage_review(db, **kwargs)
    return company, actor, contract, event, period, kwargs, review


async def test_empty_live_replay_and_no_writes(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, actor, contract, event, period, kwargs, review = await empty_case(db)
        before = await db.scalar(select(func.count()).select_from(EmploymentEvent))
        assert review['bridge'] is None
        assert review['body']['active_event_ids'] == []
        assert review['body']['active_event_count'] == 0
        assert review['body']['legal_non_applicability_confirmed'] is False
        assert review['body']['official_generation'] == 'BLOCKED'
        assert review['body']['official_rows_generated'] == 0
        assert await verify_d5_period_coverage_review(db, review) is True
        assert await build_d5_period_coverage_review(db, **{**kwargs, 'targets': ()}) == review
        assert before == await db.scalar(select(func.count()).select_from(EmploymentEvent))
        assert not db.new and not db.dirty
        await db.rollback()


@pytest.mark.parametrize('kind', ['hire', 'transfer', 'termination'])
@pytest.mark.parametrize('boundary', ['start_date', 'end_date'])
async def test_new_event_invalidates_empty_review(employee_engine, kind, boundary):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, actor, contract, event, period, kwargs, review = await empty_case(db)
        await _event(db, company_id=company.id, contract_id=contract.id,
                     actor_id=actor.id, event_type=kind, day=getattr(period, boundary))
        with pytest.raises(D5PeriodCoverageError, match='Live empty period'):
            await verify_d5_period_coverage_review(db, review)
        with pytest.raises(D5PeriodCoverageError, match='missing=1'):
            await build_d5_period_coverage_review(db, **kwargs)
        await db.rollback()


async def test_reversed_and_outside_events_do_not_populate_period(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, actor, contract, event, period, kwargs, review = await empty_case(db)
        for day in (period.start_date - timedelta(days=1), period.end_date + timedelta(days=1)):
            await _event(db, company_id=company.id, contract_id=contract.id,
                         actor_id=actor.id, event_type='hire', day=day)
        active = await _event(db, company_id=company.id, contract_id=contract.id,
                             actor_id=actor.id, event_type='transfer', day=period.start_date)
        await _event(db, company_id=company.id, contract_id=contract.id,
                     actor_id=actor.id, event_type='reversal', day=period.end_date,
                     reversal_of_id=active.id)
        assert await verify_d5_period_coverage_review(db, review) is True
        await db.rollback()


async def test_other_company_inventory_is_excluded(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, actor, contract, event, period, kwargs, review = await empty_case(db)
        other, other_actor, other_contract, _, _, _ = await setup_period(db)
        await _event(db, company_id=other.id, contract_id=other_contract.id,
                     actor_id=other_actor.id, event_type='hire', day=period.start_date)
        assert await verify_d5_period_coverage_review(db, review) is True
        with pytest.raises(D5PeriodCoverageError, match='company scope'):
            await build_d5_period_coverage_review(db, **{**kwargs, 'company_id': other.id})
        await db.rollback()


@pytest.mark.parametrize('field', ['start_date', 'end_date'])
async def test_stale_period_cache_rejected(employee_engine, field):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, actor, contract, event, period, kwargs, review = await empty_case(db)
        old = getattr(period, field)
        await db.execute(update(PayrollPeriod).where(PayrollPeriod.id == period.id)
                         .values(**{field: old + timedelta(days=1)})
                         .execution_options(synchronize_session=False))
        assert getattr(period, field) == old
        with pytest.raises(D5PeriodCoverageError, match='no longer matches'):
            await verify_d5_period_coverage_review(db, review)
        await db.rollback()


@pytest.mark.parametrize('field,value', [
    ('active_event_count', False), ('active_event_ids', [123]),
    ('candidate_target_count', 1), ('normative_approval', True),
    ('official_generation', 'READY'), ('official_rows_generated', 1),
    ('legal_non_applicability_confirmed', True), ('review_only', False),
    ('inventory_event_types', []), ('inventory_scope', 'all_legal_events'),
    ('period_start', '2000-01-01'), ('unexpected_field', 'injected'),
])
async def test_rehashed_fabrication_rejected(employee_engine, field, value):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        *_, review = await empty_case(db)
        forged = deepcopy(review)
        forged['body'][field] = value
        forged['coverage_sha256'] = _canonical_digest(forged['body'])
        with pytest.raises(D5PeriodCoverageError):
            await verify_d5_period_coverage_review(db, forged)
        await db.rollback()


@pytest.mark.parametrize('targets', [None, {}, '', [False], [{'kind': 'event', 'employment_event_id': 999999999}]])
async def test_empty_period_invalid_or_extra_targets_rejected(employee_engine, targets):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        *_, kwargs, review = await empty_case(db)
        with pytest.raises(D5PeriodCoverageError):
            await build_d5_period_coverage_review(db, **{**kwargs, 'targets': targets})
        await db.rollback()


async def test_missing_period_and_forged_bridge_rejected(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        *_, kwargs, review = await empty_case(db)
        with pytest.raises(D5PeriodCoverageError, match='company scope'):
            await build_d5_period_coverage_review(db, **{**kwargs, 'payroll_period_id': 999999999})
        review['bridge'] = {'body': {}}
        with pytest.raises(D5PeriodCoverageError, match='no longer matches'):
            await verify_d5_period_coverage_review(db, review)
        await db.rollback()
