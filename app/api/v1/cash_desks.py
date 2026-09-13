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
from app.schemas.cash_desk import (
    CashDeskCreate,
    CashDeskResponse,
    CashDeskUpdate,
)
from app.services.cash_desk_service import (
    CashDeskError,
    CashDeskNotFoundError,
    create_cash_desk,
    get_cash_desk,
    list_cash_desks,
    update_cash_desk,
)


router = APIRouter(
    prefix="/companies/{company_id}/cash-desks",
    tags=["cash-desks"],
)


def _http_error(exc: CashDeskError) -> HTTPException:
    if isinstance(exc, CashDeskNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(exc),
    )


@router.get(
    "",
    response_model=list[CashDeskResponse],
)
async def list_company_cash_desks(
    company_id: int,
    _=Depends(
        require_company_permission(
            "cash_desks.read"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    return list(
        await list_cash_desks(
            db,
            company_id=company_id,
        )
    )


@router.get(
    "/{cash_desk_id}",
    response_model=CashDeskResponse,
)
async def get_company_cash_desk(
    company_id: int,
    cash_desk_id: int,
    _=Depends(
        require_company_permission(
            "cash_desks.read"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_cash_desk(
            db,
            company_id=company_id,
            cash_desk_id=cash_desk_id,
        )
    except CashDeskError as exc:
        raise _http_error(exc) from exc


@router.post(
    "",
    response_model=CashDeskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_company_cash_desk(
    company_id: int,
    data: CashDeskCreate,
    _=Depends(
        require_company_permission(
            "cash_desks.manage"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        cash_desk = await create_cash_desk(
            db,
            company_id=company_id,
            **data.model_dump(),
        )
        await db.commit()
        await db.refresh(cash_desk)
        return cash_desk
    except CashDeskError as exc:
        await db.rollback()
        raise _http_error(exc) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cash desk data conflict",
        ) from exc
    except Exception:
        await db.rollback()
        raise


@router.patch(
    "/{cash_desk_id}",
    response_model=CashDeskResponse,
)
async def update_company_cash_desk(
    company_id: int,
    cash_desk_id: int,
    data: CashDeskUpdate,
    _=Depends(
        require_company_permission(
            "cash_desks.manage"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        values = data.model_dump(
            exclude_unset=True
        )
        cash_desk = await update_cash_desk(
            db,
            company_id=company_id,
            cash_desk_id=cash_desk_id,
            **values,
        )
        await db.commit()
        await db.refresh(cash_desk)
        return cash_desk
    except CashDeskError as exc:
        await db.rollback()
        raise _http_error(exc) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cash desk data conflict",
        ) from exc
    except Exception:
        await db.rollback()
        raise
