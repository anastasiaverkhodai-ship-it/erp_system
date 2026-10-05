from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.user import User
from app.schemas.payroll_supplement import PayrollSupplementCreate,PayrollSupplementRead
from app.services.payroll_supplement_service import (PayrollSupplementError,PayrollSupplementNotFoundError,
    create_payroll_supplement,cancel_payroll_supplement,list_payroll_supplements)
from app.services.payroll_input_service import PayrollInputError
router=APIRouter(prefix='/companies/{company_id}',tags=['Payroll supplements'])


def error(exc):
    return HTTPException(status_code=404 if isinstance(exc,PayrollSupplementNotFoundError) else 409,detail=str(exc))


async def write(db,operation):
    try:
        result=PayrollSupplementRead.model_validate(await operation)
        await db.commit();return result
    except (PayrollSupplementError,PayrollInputError) as exc:
        await db.rollback();raise error(exc) from exc
    except Exception:
        await db.rollback();raise


@router.post('/payroll-inputs/{payroll_input_id}/supplements',response_model=PayrollSupplementRead,status_code=201)
async def create(company_id:int,payroll_input_id:int,data:PayrollSupplementCreate,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.manage'))):
    return await write(db,create_payroll_supplement(db,company_id=company_id,payroll_input_id=payroll_input_id,data=data,created_by=actor.id))


@router.post('/payroll-supplements/{supplement_id}/cancel',response_model=PayrollSupplementRead)
async def cancel(company_id:int,supplement_id:int,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.manage'))):
    return await write(db,cancel_payroll_supplement(db,company_id=company_id,supplement_id=supplement_id,cancelled_by=actor.id))


@router.get('/payroll-inputs/{payroll_input_id}/supplements',response_model=list[PayrollSupplementRead])
async def read(company_id:int,payroll_input_id:int,db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.read'))):
    try: return await list_payroll_supplements(db,company_id=company_id,payroll_input_id=payroll_input_id)
    except PayrollSupplementError as exc: raise error(exc) from exc
