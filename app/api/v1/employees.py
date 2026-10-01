from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.permissions import (
    require_company_permission,
)
from app.core.database import get_db
from app.models.user import User
from app.schemas.employee import (
    EmployeeCreate,
    EmployeeResponse,
    EmployeeUpdate,
)
from app.services.employee_service import (
    EmployeeCompanyNotFoundError,
    EmployeeDuplicateError,
    EmployeeError,
    EmployeeLifecycleError,
    EmployeeNotFoundError,
    EmployeeUserLinkError,
    create_employee,
    get_employee,
    list_employees,
    update_employee,
)


router = APIRouter(
    prefix="/companies/{company_id}/employees",
    tags=["Employees"],
)


def _http_error(
    exc: EmployeeError,
) -> HTTPException:
    if isinstance(
        exc,
        (
            EmployeeNotFoundError,
            EmployeeCompanyNotFoundError,
        ),
    ):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    if isinstance(
        exc,
        EmployeeDuplicateError,
    ):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    if isinstance(
        exc,
        (
            EmployeeUserLinkError,
            EmployeeLifecycleError,
        ),
    ):
        return HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail=str(exc),
        )

    return HTTPException(
        status_code=(
            status.HTTP_422_UNPROCESSABLE_CONTENT
        ),
        detail=str(exc),
    )


@router.get(
    "",
    response_model=list[EmployeeResponse],
)
async def get_employees(
    company_id: int,
    _=Depends(
        require_company_permission(
            "employees.read"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    return await list_employees(
        db,
        company_id=company_id,
    )


@router.get(
    "/{employee_id}",
    response_model=EmployeeResponse,
)
async def get_one_employee(
    company_id: int,
    employee_id: int,
    _=Depends(
        require_company_permission(
            "employees.read"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_employee(
            db,
            company_id=company_id,
            employee_id=employee_id,
        )
    except EmployeeError as exc:
        raise _http_error(exc) from exc


@router.post(
    "",
    response_model=EmployeeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_employee(
    company_id: int,
    data: EmployeeCreate,
    current_user: User = Depends(
        require_company_permission(
            "employees.manage"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        employee = await create_employee(
            db,
            company_id=company_id,
            employee_number=data.employee_number,
            first_name=data.first_name,
            last_name=data.last_name,
            middle_name=data.middle_name,
            tax_number=data.tax_number,
            birth_date=data.birth_date,
            payment_iban=data.payment_iban,
            hire_date=data.hire_date,
            termination_date=data.termination_date,
            status=data.status,
            user_id=data.user_id,
            created_by=current_user.id,
        )

        await db.commit()
        await db.refresh(employee)

        return employee

    except EmployeeError as exc:
        await db.rollback()
        raise _http_error(exc) from exc

    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Employee data conflict",
        ) from exc

    except Exception:
        await db.rollback()
        raise


@router.patch(
    "/{employee_id}",
    response_model=EmployeeResponse,
)
async def patch_employee(
    company_id: int,
    employee_id: int,
    data: EmployeeUpdate,
    current_user: User = Depends(
        require_company_permission(
            "employees.manage"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        values = data.model_dump(
            exclude_unset=True
        )

        employee = await update_employee(
            db,
            company_id=company_id,
            employee_id=employee_id,
            changed_by=current_user.id,
            fields_set=set(data.model_fields_set),
            **values,
        )

        await db.commit()
        await db.refresh(employee)

        return employee

    except EmployeeError as exc:
        await db.rollback()
        raise _http_error(exc) from exc

    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Employee data conflict",
        ) from exc

    except Exception:
        await db.rollback()
        raise
