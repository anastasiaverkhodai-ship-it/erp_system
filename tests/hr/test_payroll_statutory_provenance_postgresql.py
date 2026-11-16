"""Live PostgreSQL source provenance; fixtures are not normative approval."""
import os
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_insurance_funding import setup
from app.models.employee import Employee
from app.models.payroll import PayrollPeriod
from app.models.payroll_regulatory_report import PayrollRegulatoryReportRow
from app.services.payroll_regulatory_readiness_service import payroll_regulatory_readiness
from app.services.payroll_regulatory_report_service import (
    create_payroll_regulatory_report, generate_payroll_regulatory_report,
    PayrollRegulatoryReportError,
)
from app.services.payroll_regulatory_persisted_source_verifier import (
    verify_persisted_payroll_sources, PersistedSourceIntegrityError,
)
from app.services import payroll_regulatory_source_snapshot_service as snapshot
from app.services import payroll_regulatory_form_row_writer as writer
from app.services import payroll_regulatory_dated_4df_service as dated

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv('RUN_POSTGRES_E2E') != '1', reason='Isolated PostgreSQL required')]

async def case(db, kind):
    assert (await db.scalar(text('SELECT current_database()'))) != 'erp_db'
    company, user, contract, calc, *_ = await setup(db)
    employee = await db.get(Employee, contract.employee_id)
    employee.tax_number = '1234567890'
    period = await db.get(PayrollPeriod, calc.payroll_period_id)
    period.status = 'finalized'
    period.finalized_by = user.id
    period.finalized_at = datetime.now(timezone.utc)
    await db.flush()
    report = await create_payroll_regulatory_report(db, company_id=company.id,
        payroll_period_id=period.id, report_kind=kind, created_by=user.id)
    readiness = await payroll_regulatory_readiness(db, company_id=company.id,
        payroll_period_id=period.id, report_kind=kind)
    return company, user, employee, report, readiness

@pytest.mark.parametrize('kind', ['d1', '4df', 'd5', 'd6'])
async def test_live_sources_snapshot_replay_and_generation_blocked(employee_engine, kind):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, user, employee, report, readiness = await case(db, kind)
        proof = await verify_persisted_payroll_sources(db, company_id=company.id,
            payroll_period_id=report.payroll_period_id, readiness=readiness)
        assert proof['verified'] and not proof['statutory_approved']
        args = dict(company_id=company.id, payroll_regulatory_report_id=report.id)
        rows = await snapshot.persist_regulatory_source_snapshot(db, **args)
        assert rows and all(r.row_code == snapshot.SOURCE_CODE for r in rows)
        assert [r.id for r in await snapshot.persist_regulatory_source_snapshot(db, **args)] == [r.id for r in rows]
        with pytest.raises(PayrollRegulatoryReportError):
            await generate_payroll_regulatory_report(db, **args, generated_by=user.id)
        assert report.status == 'draft'
        await db.rollback()

@pytest.mark.parametrize('kind', ['d1', '4df', 'd5', 'd6'])
async def test_identity_map_cannot_hide_live_identity_change(employee_engine, kind):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, user, employee, report, readiness = await case(db, kind)
        await db.execute(text('UPDATE employees SET tax_number=:number WHERE id=:id'),
            {'number': '9876543210', 'id': employee.id})
        assert employee.tax_number == '1234567890'  # Deliberately stale ORM object.
        with pytest.raises(PersistedSourceIntegrityError, match='tax_number'):
            await verify_persisted_payroll_sources(db, company_id=company.id,
                payroll_period_id=report.payroll_period_id, readiness=readiness)
        await db.rollback()

@pytest.mark.parametrize('target,kind', [('snapshot', k) for k in ('d1','4df','d5','d6')] + [('writer','d1'),('writer','4df'),('dated','4df')])
async def test_forged_projection_never_persists(employee_engine, monkeypatch, target, kind):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, user, employee, report, readiness = await case(db, kind)
        forged = deepcopy(readiness)
        # Change gross and net together: arithmetic reconciliation alone is insufficient.
        forged['calculation_sources'][0]['gross_amount'] += Decimal('1.00')
        forged['calculation_sources'][0]['net_amount'] += Decimal('1.00')
        async def forged_readiness(*args, **kwargs):
            return forged
        module = {'snapshot': snapshot, 'writer': writer, 'dated': dated}[target]
        monkeypatch.setattr(module, 'payroll_regulatory_readiness', forged_readiness)
        args = dict(company_id=company.id, payroll_regulatory_report_id=report.id)
        with pytest.raises(PayrollRegulatoryReportError, match='Persisted statutory mismatch'):
            if target == 'snapshot':
                await snapshot.persist_regulatory_source_snapshot(db, **args)
            elif target == 'dated':
                await dated.persist_dated_4df_rows(db, **args, reviewed_allocations=[])
            else:
                await writer.persist_reviewed_form_rows(db, **args, reviewed_allocations=[])
        assert await db.scalar(select(func.count()).select_from(PayrollRegulatoryReportRow).where(
            PayrollRegulatoryReportRow.payroll_regulatory_report_id == report.id)) == 0
        await db.rollback()

@pytest.mark.parametrize('mutation', ['company', 'period', 'duplicate', 'component', 'empty'])
async def test_scope_and_component_tampering_rejected(employee_engine, mutation):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, user, employee, report, readiness = await case(db, 'd1')
        bad = deepcopy(readiness)
        if mutation == 'company': bad['company_id'] += 1000000
        elif mutation == 'period': bad['payroll_period_id'] += 1000000
        elif mutation == 'duplicate': bad['calculation_sources'] *= 2
        elif mutation == 'component': bad['calculation_sources'][0]['components'][0]['amount'] += Decimal('1.00')
        else: bad['calculation_sources'] = []
        with pytest.raises(PersistedSourceIntegrityError):
            await verify_persisted_payroll_sources(db, company_id=company.id,
                payroll_period_id=report.payroll_period_id, readiness=bad)
        await db.rollback()

@pytest.mark.parametrize('kind', ['d1', '4df', 'd5', 'd6'])
async def test_replay_checks_database_rows_not_cached_payload(employee_engine, kind):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, user, employee, report, readiness = await case(db, kind)
        args = dict(company_id=company.id, payroll_regulatory_report_id=report.id)
        rows = await snapshot.persist_regulatory_source_snapshot(db, **args)
        original = rows[0].payload_json
        await db.execute(text("UPDATE payroll_regulatory_report_rows SET payload_json='{}' WHERE id=:id"),
            {'id': rows[0].id})
        assert rows[0].payload_json == original
        with pytest.raises(PayrollRegulatoryReportError, match='differs'):
            await snapshot.persist_regulatory_source_snapshot(db, **args)
        await db.rollback()

@pytest.mark.parametrize('target', ['snapshot', 'writer'])
async def test_cached_draft_cannot_rewrite_generated_report(employee_engine, target):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, user, employee, report, readiness = await case(db, 'd1')
        await db.execute(text("UPDATE payroll_regulatory_reports SET status='generated', generated_at=CURRENT_TIMESTAMP, generated_by=:actor WHERE id=:id"),
            {'id': report.id, 'actor': user.id})
        assert report.status == 'draft'
        args = dict(company_id=company.id, payroll_regulatory_report_id=report.id)
        with pytest.raises(PayrollRegulatoryReportError, match='[Gg]enerated'):
            if target == 'snapshot':
                await snapshot.persist_regulatory_source_snapshot(db, **args)
            else:
                await writer.persist_reviewed_form_rows(db, **args, reviewed_allocations=[])
        await db.rollback()
