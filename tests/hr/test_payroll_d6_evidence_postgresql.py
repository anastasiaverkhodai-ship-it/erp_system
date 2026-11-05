"""Real PostgreSQL lifecycle, corrections, tenant isolation and snapshot binding."""
import os
from datetime import date
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_regulatory_readiness_postgresql import seed
from app.models.user import User
from app.models.user_company_role import UserCompanyRole
from app.schemas.payroll_d6_evidence import D6EvidenceCreate, D6EvidenceReviewCreate
from app.services.payroll_d6_evidence_service import create_evidence, review_evidence, period_evidence
from app.services.payroll_regulatory_d5_d6_workflow import build_verified_d5_d6_workflow, compare_hr_reviews

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Isolated PostgreSQL required')]


def request(contract,calc,key,previous=None,applies=True):
    return D6EvidenceCreate(payroll_period_id=calc.payroll_period_id,employment_contract_id=contract.id,
        supersedes_id=previous,applies=applies,document_date=date(2026,9,30),
        document_reference='Documented special-service assessment',request_key=key,
        intervals=[dict(start_date=date(2026,9,1),end_date=date(2026,9,30),basis_reference='Verified employment source document',
            unit='days',quantity='15',normal_quantity='30')] if applies else [])


async def reviewer_id(db,company,user):
    return await db.scalar(select(UserCompanyRole.user_id).where(UserCompanyRole.company_id==company.id,UserCompanyRole.user_id!=user.id))


async def approve(db,company,reviewer,item):
    return await review_evidence(db,company_id=company.id,actor_id=reviewer,evidence_id=item.id,
        data=D6EvidenceReviewCreate(approved=True,review_reference='Checked against original personnel documents'))


async def test_full_review_correction_and_snapshot(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,*_=await seed(db)
        reviewer=await reviewer_id(db,company,user)
        before=await build_verified_d5_d6_workflow(db,company_id=company.id,payroll_period_id=calc.payroll_period_id)
        assert before['d6']['applicability']=='NOT_DETERMINED'
        data=request(contract,calc,'d6-original')
        item=await create_evidence(db,company_id=company.id,actor_id=user.id,data=data)
        assert (await create_evidence(db,company_id=company.id,actor_id=user.id,data=data)).id==item.id
        with pytest.raises(ValueError,match='Independent'):
            await approve(db,company,user.id,item)
        assert (await period_evidence(db,company_id=company.id,payroll_period_id=calc.payroll_period_id))['coverage']=='INCOMPLETE'
        review=await approve(db,company,reviewer,item)
        assert (await approve(db,company,reviewer,item)).id==review.id
        after=await build_verified_d5_d6_workflow(db,company_id=company.id,payroll_period_id=calc.payroll_period_id)
        assert after['d6']['applicability']=='APPLICABLE'
        assert after['d6']['source_coverage']=='COMPLETE'
        from app.services.payroll_regulatory_readiness_service import payroll_regulatory_readiness
        readiness=await payroll_regulatory_readiness(db,company_id=company.id,payroll_period_id=calc.payroll_period_id,report_kind='d6')
        assert readiness['applicability']=='applicable'
        assert 'D6_APPLICABILITY_AND_SPECIAL_SERVICE_EVIDENCE_MISSING' not in readiness['blocking_issues']
        assert 'DATED_STATUTORY_FORM_MAPPING_MISSING' in readiness['blocking_issues']
        assert readiness['ready'] is False
        diff=compare_hr_reviews(before,after)
        assert any(x['source_table']=='payroll_d6_evidence' for x in diff['changes'])
        assert not diff['statutory_correction_generated']
        changed=await create_evidence(db,company_id=company.id,actor_id=user.id,data=request(contract,calc,'d6-corrected',item.id,False))
        await approve(db,company,reviewer,changed)
        final=await period_evidence(db,company_id=company.id,payroll_period_id=calc.payroll_period_id)
        assert final['applicability']=='NOT_APPLICABLE'
        assert final['records'][0]['revision']==2
        assert final['official_statutory_generation']=='BLOCKED'
        assert item.payload['applies'] is True
        await db.commit()


async def test_stale_parallel_correction_and_request_conflicts(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,*_=await seed(db)
        reviewer=await reviewer_id(db,company,user)
        first=await create_evidence(db,company_id=company.id,actor_id=user.id,data=request(contract,calc,'d6-first'))
        await approve(db,company,reviewer,first)
        a=await create_evidence(db,company_id=company.id,actor_id=user.id,data=request(contract,calc,'d6-second',first.id))
        b=await create_evidence(db,company_id=company.id,actor_id=user.id,data=request(contract,calc,'d6-competing',first.id,False))
        await approve(db,company,reviewer,a)
        with pytest.raises(ValueError,match='Stale'):
            await approve(db,company,reviewer,b)
        with pytest.raises(ValueError,match='Request key'):
            await create_evidence(db,company_id=company.id,actor_id=user.id,data=request(contract,calc,'d6-second',first.id,False))
        with pytest.raises(DBAPIError,match='Stale'):
            async with db.begin_nested():
                await db.execute(text("INSERT INTO payroll_d6_evidence_reviews (company_id,evidence_id,decision,review_reference,reviewed_by) VALUES (:c,:e,'approved','Direct stale review attempt',:u)"),dict(c=company.id,e=b.id,u=reviewer))
        await db.commit()


@pytest.mark.parametrize('table',['payroll_d6_evidence','payroll_d6_evidence_reviews'])
async def test_database_immutability(employee_engine,table):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,*_=await seed(db)
        item=await create_evidence(db,company_id=company.id,actor_id=user.id,data=request(contract,calc,'d6-frozen'))
        reviewer=await reviewer_id(db,company,user)
        with pytest.raises(DBAPIError,match='Independent'):
            async with db.begin_nested():
                await db.execute(text("INSERT INTO payroll_d6_evidence_reviews (company_id,evidence_id,decision,review_reference,reviewed_by) VALUES (:c,:e,'approved','Direct self review attempt',:u)"),dict(c=company.id,e=item.id,u=user.id))
        await approve(db,company,reviewer,item)
        for sql in (f'UPDATE {table} SET company_id=company_id',f'DELETE FROM {table}',f'TRUNCATE {table} CASCADE'):
            with pytest.raises(DBAPIError,match='immutable'):
                async with db.begin_nested():
                    await db.execute(text(sql))
        await db.commit()


async def test_period_tenant_and_interval_scope(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,*_=await seed(db)
        payload=request(contract,calc,'d6-invalid').model_dump(mode='json')
        payload['intervals'][0]['start_date']='2026-08-01'
        with pytest.raises(ValueError,match='outside'):
            await create_evidence(db,company_id=company.id,actor_id=user.id,data=D6EvidenceCreate.model_validate(payload))
        with pytest.raises(ValueError,match='Permission denied'):
            await create_evidence(db,company_id=company.id+10000,actor_id=user.id,data=request(contract,calc,'d6-foreign'))
        with pytest.raises(ValueError,match='period not found'):
            await period_evidence(db,company_id=company.id+10000,payroll_period_id=calc.payroll_period_id)


async def test_reviewed_facts_persist_in_immutable_snapshots(employee_engine):
    from test_payroll_regulatory_hr_snapshot_store_postgresql import create_report
    from app.services.payroll_regulatory_hr_snapshot_store import persist_hr_review_snapshot, load_hr_snapshot, compare_persisted_hr_snapshots
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,*_=await seed(db)
        reviewer=await reviewer_id(db,company,user)
        original=await create_evidence(db,company_id=company.id,actor_id=user.id,data=request(contract,calc,'d6-snapshot-original'))
        await approve(db,company,reviewer,original)
        report=await create_report(db,company,user,calc,'d6',1)
        before=await persist_hr_review_snapshot(db,company_id=company.id,payroll_regulatory_report_id=report.id)
        corrected=await create_evidence(db,company_id=company.id,actor_id=user.id,data=request(contract,calc,'d6-snapshot-correction',original.id,False))
        await approve(db,company,reviewer,corrected)
        report2=await create_report(db,company,user,calc,'d6',2)
        after=await persist_hr_review_snapshot(db,company_id=company.id,payroll_regulatory_report_id=report2.id)
        loaded=await load_hr_snapshot(db,company_id=company.id,snapshot_id=before['id'])
        assert loaded['payload']['review']['snapshot']['d6_evidence']['records'][0]['payload']['applies'] is True
        compared=await compare_persisted_hr_snapshots(db,company_id=company.id,previous_snapshot_id=before['id'],current_snapshot_id=after['id'])
        changes=[x for x in compared['changes'] if x['source_table']=='payroll_d6_evidence']
        assert {x['change_type'] for x in changes}=={'ADDED_SOURCE','REMOVED_SOURCE'}
        assert compared['official_statutory_generation']=='BLOCKED'
        await db.commit()


async def test_rejection_does_not_replace_approved_evidence(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,*_=await seed(db)
        reviewer=await reviewer_id(db,company,user)
        original=await create_evidence(db,company_id=company.id,actor_id=user.id,data=request(contract,calc,'d6-approved-first'))
        await approve(db,company,reviewer,original)
        proposal=await create_evidence(db,company_id=company.id,actor_id=user.id,data=request(contract,calc,'d6-rejected-next',original.id,False))
        await review_evidence(db,company_id=company.id,actor_id=reviewer,evidence_id=proposal.id,
            data=D6EvidenceReviewCreate(approved=False,review_reference='Supporting document did not establish the change'))
        summary=await period_evidence(db,company_id=company.id,payroll_period_id=calc.payroll_period_id)
        assert summary['records'][0]['evidence_id']==original.id
        with pytest.raises(ValueError,match='already reviewed'):
            await approve(db,company,reviewer,proposal)
        replacement=await create_evidence(db,company_id=company.id,actor_id=user.id,data=request(contract,calc,'d6-retry-after-rejection',original.id,False))
        await approve(db,company,reviewer,replacement)
        assert replacement.revision==3
        await db.commit()


async def test_missing_migration_is_explicit_and_other_reporting_stays_available(employee_engine):
    from app.services.payroll_d6_evidence_service import D6MigrationRequired
    from app.services.payroll_regulatory_readiness_service import payroll_regulatory_readiness
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,*_=await seed(db)
        # Simulate the explicitly deferred production migration in this disposable schema.
        await db.execute(text('DROP TABLE payroll_d6_evidence_reviews'))
        await db.execute(text('DROP TABLE payroll_d6_evidence'))
        summary=await period_evidence(db,company_id=company.id,payroll_period_id=calc.payroll_period_id)
        assert summary['coverage']=='MIGRATION_REQUIRED'
        assert summary['applicability']=='NOT_DETERMINED'
        ready=await payroll_regulatory_readiness(db,company_id=company.id,payroll_period_id=calc.payroll_period_id,report_kind='d6')
        assert 'D6_EVIDENCE_MIGRATION_REQUIRED' in ready['blocking_issues']
        assert ready['ready'] is False
        with pytest.raises(D6MigrationRequired):
            await create_evidence(db,company_id=company.id,actor_id=user.id,data=request(contract,calc,'d6-no-migration'))
        other=await payroll_regulatory_readiness(db,company_id=company.id,payroll_period_id=calc.payroll_period_id,report_kind='d1')
        assert other['report_kind']=='d1'
