from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.user import User
from app.schemas.hr_change import HRChangeResponse
from app.services.hr_change_service import list_changes
from app.services.employee_service import get_employee, EmployeeError
from app.services.employment_structure_service import get_department, get_position, get_employment_contract, EmploymentStructureError
from app.services.employee_salary_rate_service import get_salary_rate

router = APIRouter(prefix='/companies/{company_id}', tags=['HR history'],
    dependencies=[Depends(require_company_permission('employees.read'))])


async def _history(db, company_id, entity_id, entity_type, getter, id_field):
    try:
        await getter(db, company_id=company_id, **{id_field: entity_id})
    except (EmployeeError, EmploymentStructureError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await list_changes(db, company_id, entity_type, entity_id)


@router.get('/employees/{employee_id}/history', response_model=list[HRChangeResponse])
async def employee_history(company_id: int, employee_id: int, db: AsyncSession = Depends(get_db)):
    return await _history(db, company_id, employee_id, 'employee', get_employee, 'employee_id')


@router.get('/departments/{department_id}/history', response_model=list[HRChangeResponse])
async def department_history(company_id: int, department_id: int, db: AsyncSession = Depends(get_db)):
    return await _history(db, company_id, department_id, 'department', get_department, 'department_id')


@router.get('/positions/{position_id}/history', response_model=list[HRChangeResponse])
async def position_history(company_id: int, position_id: int, db: AsyncSession = Depends(get_db)):
    return await _history(db, company_id, position_id, 'position', get_position, 'position_id')


@router.get('/employment-contracts/{contract_id}/history', response_model=list[HRChangeResponse])
async def contract_history(company_id: int, contract_id: int, db: AsyncSession = Depends(get_db)):
    return await _history(db, company_id, contract_id, 'employment_contract', get_employment_contract, 'contract_id')

@router.get(
    "/salary-rates/{salary_rate_id}/history",
    response_model=list[HRChangeResponse],
)
async def salary_rate_history(
    company_id: int,
    salary_rate_id: int,
    _: User = Depends(
        require_company_permission("employees.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    await get_salary_rate(
        db,
        company_id=company_id,
        salary_rate_id=salary_rate_id,
    )

    return await list_changes(
        db,
        company_id=company_id,
        entity_type="salary_rate",
        entity_id=salary_rate_id,
    )
