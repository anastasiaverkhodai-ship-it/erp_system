from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.user import User
from app.schemas.employment_structure import (
    DepartmentCreate,
    DepartmentResponse,
    DepartmentUpdate,
    EmploymentContractCreate,
    EmploymentContractResponse,
    EmploymentContractUpdate,
    PositionCreate,
    PositionResponse,
    PositionUpdate,
)
from app.services.employment_structure_service import (
    EmploymentStructureDuplicateError,
    EmploymentStructureError,
    EmploymentStructureLifecycleError,
    EmploymentStructureNotFoundError,
    create_department,
    create_employment_contract,
    create_position,
    get_department,
    get_employment_contract,
    get_position,
    list_departments,
    list_employment_contracts,
    list_positions,
    update_department,
    update_employment_contract,
    update_position,
)


router = APIRouter(
    prefix="/companies/{company_id}",
    tags=["Employment Structure"],
)


def _http_error(
    exc: EmploymentStructureError,
) -> HTTPException:
    if isinstance(
        exc,
        EmploymentStructureNotFoundError,
    ):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    if isinstance(
        exc,
        EmploymentStructureDuplicateError,
    ):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    if isinstance(
        exc,
        EmploymentStructureLifecycleError,
    ):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        )

    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=str(exc),
    )


@router.get(
    "/departments",
    response_model=list[DepartmentResponse],
)
async def get_departments(
    company_id: int,
    _=Depends(
        require_company_permission("employees.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    return await list_departments(
        db,
        company_id=company_id,
    )


@router.post(
    "/departments",
    response_model=DepartmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_department(
    company_id: int,
    data: DepartmentCreate,
    current_user: User = Depends(
        require_company_permission("employees.manage")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        obj = await create_department(
            db,
            company_id=company_id,
            created_by=current_user.id,
            **data.model_dump(),
        )
        await db.commit()
        await db.refresh(obj)
        return obj
    except EmploymentStructureError as exc:
        await db.rollback()
        raise _http_error(exc) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Department data conflict",
        ) from exc
    except Exception:
        await db.rollback()
        raise


@router.get(
    "/departments/{department_id}",
    response_model=DepartmentResponse,
)
async def get_one_department(
    company_id: int,
    department_id: int,
    _=Depends(
        require_company_permission("employees.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_department(
            db,
            company_id=company_id,
            department_id=department_id,
        )
    except EmploymentStructureError as exc:
        raise _http_error(exc) from exc


@router.patch(
    "/departments/{department_id}",
    response_model=DepartmentResponse,
)
async def patch_department(
    company_id: int,
    department_id: int,
    data: DepartmentUpdate,
    current_user: User = Depends(
        require_company_permission("employees.manage")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        obj = await update_department(
            db,
            company_id=company_id,
            department_id=department_id,
            changed_by=current_user.id,
            fields_set=set(data.model_fields_set),
            **data.model_dump(exclude_unset=True),
        )
        await db.commit()
        await db.refresh(obj)
        return obj
    except EmploymentStructureError as exc:
        await db.rollback()
        raise _http_error(exc) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Department data conflict",
        ) from exc
    except Exception:
        await db.rollback()
        raise


@router.get(
    "/positions",
    response_model=list[PositionResponse],
)
async def get_positions(
    company_id: int,
    _=Depends(
        require_company_permission("employees.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    return await list_positions(
        db,
        company_id=company_id,
    )


@router.post(
    "/positions",
    response_model=PositionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_position(
    company_id: int,
    data: PositionCreate,
    current_user: User = Depends(
        require_company_permission("employees.manage")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        obj = await create_position(
            db,
            company_id=company_id,
            created_by=current_user.id,
            **data.model_dump(),
        )
        await db.commit()
        await db.refresh(obj)
        return obj
    except EmploymentStructureError as exc:
        await db.rollback()
        raise _http_error(exc) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Position data conflict",
        ) from exc
    except Exception:
        await db.rollback()
        raise


@router.get(
    "/positions/{position_id}",
    response_model=PositionResponse,
)
async def get_one_position(
    company_id: int,
    position_id: int,
    _=Depends(
        require_company_permission("employees.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_position(
            db,
            company_id=company_id,
            position_id=position_id,
        )
    except EmploymentStructureError as exc:
        raise _http_error(exc) from exc


@router.patch(
    "/positions/{position_id}",
    response_model=PositionResponse,
)
async def patch_position(
    company_id: int,
    position_id: int,
    data: PositionUpdate,
    current_user: User = Depends(
        require_company_permission("employees.manage")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        obj = await update_position(
            db,
            company_id=company_id,
            position_id=position_id,
            changed_by=current_user.id,
            fields_set=set(data.model_fields_set),
            **data.model_dump(exclude_unset=True),
        )
        await db.commit()
        await db.refresh(obj)
        return obj
    except EmploymentStructureError as exc:
        await db.rollback()
        raise _http_error(exc) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Position data conflict",
        ) from exc
    except Exception:
        await db.rollback()
        raise


@router.get(
    "/employment-contracts",
    response_model=list[EmploymentContractResponse],
)
async def get_contracts(
    company_id: int,
    employee_id: int | None = Query(
        default=None,
        gt=0,
    ),
    _=Depends(
        require_company_permission("employees.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    return await list_employment_contracts(
        db,
        company_id=company_id,
        employee_id=employee_id,
    )


@router.get(
    "/employment-contracts/{contract_id}",
    response_model=EmploymentContractResponse,
)
async def get_one_contract(
    company_id: int,
    contract_id: int,
    _=Depends(
        require_company_permission("employees.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_employment_contract(
            db,
            company_id=company_id,
            contract_id=contract_id,
        )
    except EmploymentStructureError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/employment-contracts",
    response_model=EmploymentContractResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_contract(
    company_id: int,
    data: EmploymentContractCreate,
    current_user: User = Depends(
        require_company_permission("employees.manage")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        obj = await create_employment_contract(
            db,
            company_id=company_id,
            created_by=current_user.id,
            **data.model_dump(),
        )
        await db.commit()
        await db.refresh(obj)
        return obj
    except EmploymentStructureError as exc:
        await db.rollback()
        raise _http_error(exc) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Employment contract data conflict",
        ) from exc
    except Exception:
        await db.rollback()
        raise


@router.patch(
    "/employment-contracts/{contract_id}",
    response_model=EmploymentContractResponse,
)
async def patch_contract(
    company_id: int,
    contract_id: int,
    data: EmploymentContractUpdate,
    current_user: User = Depends(
        require_company_permission("employees.manage")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        obj = await update_employment_contract(
            db,
            company_id=company_id,
            contract_id=contract_id,
            changed_by=current_user.id,
            fields_set=set(data.model_fields_set),
            **data.model_dump(exclude_unset=True),
        )
        await db.commit()
        await db.refresh(obj)
        return obj
    except EmploymentStructureError as exc:
        await db.rollback()
        raise _http_error(exc) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Employment contract data conflict",
        ) from exc
    except Exception:
        await db.rollback()
        raise
