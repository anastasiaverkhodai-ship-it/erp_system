"""Real PostgreSQL declaration lifecycle, carry chain and rollback checks."""
import importlib.util
import os
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal as D
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.pool import NullPool
from alembic.operations import Operations
from alembic.migration import MigrationContext
import app.models
from app.core.database import Base
from app.core.config import settings
from app.models.vat_declaration import VatDeclaration
from app.models.vat_declaration_source_line import VatDeclarationSourceLine
from app.models.vat_declaration_status_event import VatDeclarationStatusEvent
from app.services.vat_declaration_orchestration_service import build_vat_declaration_idempotent as build
from app.services.vat_declaration_lifecycle_service import append_vat_declaration_status as status
from app.services.input_vat_credit_claim_service import create_input_vat_credit_claim
from app.services.tax_invoice_input_persistence_service import create_input_tax_invoice, InputTaxInvoiceLineAttestation
from app.api.v1.vat_declarations import _load_detail
from app.schemas.vat_declaration import VatDeclarationDetailRead
from test_tax_invoice_postgresql import load_vat_seed_module
from test_input_vat_credit_eligibility import payload

pytestmark=pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Set RUN_POSTGRES_E2E=1')

@pytest.mark.asyncio
async def test_declaration_real_credit_carry_lifecycle_and_migrations():
    engine=create_async_engine(settings.database_url,poolclass=NullPool)
    schema='test_declaration_'+uuid4().hex
    try:
        async with engine.connect() as conn:
            tx=await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await conn.run_sync(Base.metadata.create_all)
                def cycle(c):
                    modules=[]
                    for prefix in ('f7c8d9e0a142','c3f0a1b2d475'):
                        path=next((Path(__file__).parents[1]/'alembic/versions').glob(prefix+'*.py'))
                        spec=importlib.util.spec_from_file_location(prefix,path)
                        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);modules.append(module)
                    with Operations.context(MigrationContext.configure(c)):
                        modules[1].downgrade();modules[0].downgrade()
                        modules[0].upgrade();modules[1].upgrade()
                await conn.run_sync(cycle)
                async with AsyncSession(conn,expire_on_commit=False) as db:
                    seed=load_vat_seed_module()
                    seed.D1,seed.D2,seed.D3=(date(2026,7,i) for i in (1,2,3))
                    allocation,_=await seed.seed(db,True,True)
                    await db.execute(text("UPDATE accounting_periods SET month=7,end_date='2026-07-31'"))
                    await db.execute(text("DELETE FROM tax_credit_evidence"))
                    await db.execute(text("UPDATE companies SET edrpou='12345678',vat_number='111'"))
                    await db.execute(text("UPDATE counterparties SET tax_number='87654321',vat_number='222'"))
                    db.add(allocation);await db.flush()
                    claim=await create_input_vat_credit_claim(db,company_id=1,data=payload(invoice_date=seed.D1,
                        registered_on=seed.D1,claim_period=seed.D1),created_by=1)
                    await create_input_tax_invoice(db,company_id=1,lines=[InputTaxInvoiceLineAttestation(
                        line_number=1,claim_id=claim.id,description='Goods',quantity=D(6),uom_code='pcs',classification_kind='uktzed',statutory_code='1234567890')],created_by=1)
                    cutoff=datetime.now(timezone.utc)
                    args=dict(company_id=1,reporting_year=2026,reporting_month=7,source_cutoff_at=cutoff,created_by=1,idempotency_key='july')
                    july=await build(db,**args)
                    assert july.input_vat_credit==D(12) and july.closing_negative_carry==D(12)
                    assert (await build(db,**args)).id==july.id
                    with pytest.raises(HTTPException,match='already used|different|reused|reuse'):
                        async with db.begin_nested():
                            await build(db,**{**args,'source_cutoff_at':cutoff-timedelta(seconds=1)})
                    detail=await _load_detail(db,company_id=1,declaration_id=july.id)
                    response=VatDeclarationDetailRead.model_validate(detail)
                    assert len(response.source_lines)==1 and len(response.status_events)==1
                    with pytest.raises(HTTPException):
                        await _load_detail(db,company_id=2,declaration_id=july.id)
                    # Rebuild is a separate immutable draft; old draft cannot finalize.
                    newer=await build(db,**{**args,'idempotency_key':'july-v2'})
                    assert newer.snapshot_version==2 and newer.supersedes_declaration_id==july.id
                    with pytest.raises(HTTPException,match='Superseded'):
                        await status(db=db,company_id=1,vat_declaration_id=july.id,new_status='finalized',event_date=date.today(),created_by=1)
                    await db.execute(text("INSERT INTO accounting_periods(company_id,year,month,start_date,end_date,status,is_locked,created_at) VALUES(1,2026,8,'2026-08-01','2026-08-31','open',false,CURRENT_TIMESTAMP)"))
                    august_args={**args,'reporting_month':8,'idempotency_key':'august','source_cutoff_at':datetime.now(timezone.utc)}
                    with pytest.raises(HTTPException,match='must be accepted'):
                        async with db.begin_nested():
                            await build(db,**august_args)
                    for state in ('finalized','submitted','accepted'):
                        event=await status(db=db,company_id=1,vat_declaration_id=newer.id,new_status=state,event_date=date.today(),reference='receipt' if state!='finalized' else None,created_by=1)
                        repeated=await status(db=db,company_id=1,vat_declaration_id=newer.id,new_status=state,event_date=date.today(),reference='receipt' if state!='finalized' else None,created_by=1)
                        assert event.id==repeated.id
                    august=await build(db,**{**august_args,'source_cutoff_at':datetime.now(timezone.utc)})
                    assert august.opening_negative_carry==D(12) and august.closing_negative_carry==D(12)
                    assert august.current_period_negative==0
                    detail=await _load_detail(db,company_id=1,declaration_id=august.id)
                    assert len(detail.carry_forward_lines)==1
                    assert detail.carry_forward_lines[0].origin_reporting_month==7
                    with pytest.raises(HTTPException,match='Later declaration'):
                        async with db.begin_nested():
                            await build(db,**{**args,'idempotency_key':'invalid-backwards'})
                    assert await db.scalar(select(func.count()).select_from(VatDeclaration))==3
                    with pytest.raises(HTTPException,match='future'):
                        await status(db=db,company_id=1,vat_declaration_id=august.id,new_status='finalized',event_date=date.today()+timedelta(days=1),created_by=1)
                    # Header/source snapshots are unaffected by status changes.
                    assert await db.scalar(select(func.count()).select_from(VatDeclarationSourceLine))==2
            finally:
                await tx.rollback()
    finally:
        await engine.dispose()
