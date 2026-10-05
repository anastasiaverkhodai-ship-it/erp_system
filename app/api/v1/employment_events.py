from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.user import User
from app.schemas.employment_event import EmploymentEventCreate, EmploymentEventRead, EmploymentEventReverse, EmploymentEventBatch
from app.services.employment_structure_service import EmploymentStructureError
from app.api.v1.employment_structure import _http_error
from app.services.employment_event_service import (
    list_employment_events, create_employment_event, employment_state_on,
    reverse_employment_event, create_employment_event_batch,
)

router=APIRouter(prefix='/companies/{company_id}',tags=['Employment events'])


@router.get('/employment-contracts/{contract_id}/events',response_model=list[EmploymentEventRead])
async def read_events(company_id:int,contract_id:int,db:AsyncSession=Depends(get_db),
                      actor:User=Depends(require_company_permission('employees.read'))):
    try:
        return await list_employment_events(db,company_id=company_id,contract_id=contract_id)
    except EmploymentStructureError as exc:
        raise _http_error(exc) from exc


@router.get('/employment-contracts/{contract_id}/state')
async def read_state(company_id:int,contract_id:int,on_date:date,db:AsyncSession=Depends(get_db),
                     actor:User=Depends(require_company_permission('employees.read'))):
    try:
        return await employment_state_on(db,company_id=company_id,contract_id=contract_id,on_date=on_date)
    except EmploymentStructureError as exc:
        raise _http_error(exc) from exc


async def _write(db, operation):
    try:
        result=await operation
        # Materialize before commit, so serialization cannot fail after a write.
        output=[EmploymentEventRead.model_validate(row) for row in result] if isinstance(result,list) else EmploymentEventRead.model_validate(result)
        await db.commit()
        return output
    except EmploymentStructureError as exc:
        await db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        await db.rollback()
        raise


@router.post('/employment-contracts/{contract_id}/events',response_model=EmploymentEventRead,status_code=201)
async def post_event(company_id:int,contract_id:int,data:EmploymentEventCreate,
                     db:AsyncSession=Depends(get_db),actor:User=Depends(require_company_permission('employees.manage'))):
    return await _write(db,create_employment_event(db,company_id=company_id,
        contract_id=contract_id,data=data,created_by=actor.id))


@router.post('/employment-contracts/{contract_id}/events/{event_id}/reverse',response_model=EmploymentEventRead,status_code=201)
async def reverse_event(company_id:int,contract_id:int,event_id:int,data:EmploymentEventReverse,
                        db:AsyncSession=Depends(get_db),actor:User=Depends(require_company_permission('employees.manage'))):
    return await _write(db,reverse_employment_event(db,company_id=company_id,
        contract_id=contract_id,event_id=event_id,data=data,created_by=actor.id))


@router.post('/employment-events/batch',response_model=list[EmploymentEventRead],status_code=201)
async def post_event_batch(company_id:int,data:EmploymentEventBatch,db:AsyncSession=Depends(get_db),
                           actor:User=Depends(require_company_permission('employees.manage'))):
    return await _write(db,create_employment_event_batch(db,company_id=company_id,data=data,created_by=actor.id))
