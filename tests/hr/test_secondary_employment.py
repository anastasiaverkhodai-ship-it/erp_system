"""Primary/internal/external employment and safe primary termination."""
import os
from datetime import date
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_employment_structure import setup, contract
from app.services.employment_structure_service import (
    update_employment_contract, EmploymentStructureLifecycleError,
    EmploymentStructureDuplicateError,
)
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]

async def test_primary_and_internal_secondary_have_distinct_lifecycles(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f=await setup(db)
        primary=await contract(db,f)
        secondary=await contract(db,f,number='SECONDARY',employment_kind='internal_secondary')
        with pytest.raises(EmploymentStructureDuplicateError):
            async with db.begin_nested():
                await contract(db,f,number='DUPLICATE')
        with pytest.raises(EmploymentStructureLifecycleError,match='covering'):
            async with db.begin_nested():
                await update_employment_contract(db,company_id=f['company'],contract_id=primary.id,
                    end_date=date(2026,9,30),status='ended',fields_set={'end_date','status'},changed_by=f['actor'])
        await update_employment_contract(db,company_id=f['company'],contract_id=secondary.id,
            end_date=date(2026,9,30),status='ended',fields_set={'end_date','status'},changed_by=f['actor'])
        await update_employment_contract(db,company_id=f['company'],contract_id=primary.id,
            end_date=date(2026,9,30),status='ended',fields_set={'end_date','status'},changed_by=f['actor'])
        assert secondary.end_date==primary.end_date

async def test_secondary_kind_requires_consistent_primary_employer(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f=await setup(db)
        with pytest.raises(EmploymentStructureLifecycleError,match='primary'):
            async with db.begin_nested():
                await contract(db,f,employment_kind='internal_secondary')
        await contract(db,f,employment_kind='external_secondary')
        with pytest.raises(EmploymentStructureLifecycleError,match='internal'):
            async with db.begin_nested():
                await contract(db,f,number='PRIMARY',employment_kind='primary')
