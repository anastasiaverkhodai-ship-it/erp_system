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
from app.schemas.bank_account import (
    BankAccountCreate,
    BankAccountResponse,
    BankAccountUpdate,
)
from app.services.bank_account_service import (
    BankAccountAccountingAccountError,
    BankAccountCompanyNotFoundError,
    BankAccountDuplicateError,
    BankAccountError,
    BankAccountNotFoundError,
    create_bank_account,
    get_bank_account,
    list_bank_accounts,
    update_bank_account,
)


router = APIRouter(
    prefix="/companies/{company_id}/bank-accounts",
    tags=["Bank Accounts"],
)


def _http_error(
    exc: BankAccountError,
) -> HTTPException:
    if isinstance(
        exc,
        BankAccountNotFoundError,
    ):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    if isinstance(
        exc,
        BankAccountCompanyNotFoundError,
    ):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    if isinstance(
        exc,
        BankAccountDuplicateError,
    ):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    if isinstance(
        exc,
        BankAccountAccountingAccountError,
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
    response_model=list[BankAccountResponse],
)
async def get_bank_accounts(
    company_id: int,
    _=Depends(
        require_company_permission(
            "bank_accounts.read"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    return await list_bank_accounts(
        db,
        company_id=company_id,
    )


@router.get(
    "/{bank_account_id}",
    response_model=BankAccountResponse,
)
async def get_one_bank_account(
    company_id: int,
    bank_account_id: int,
    _=Depends(
        require_company_permission(
            "bank_accounts.read"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_bank_account(
            db,
            company_id=company_id,
            bank_account_id=bank_account_id,
        )
    except BankAccountError as exc:
        raise _http_error(exc) from exc


@router.post(
    "",
    response_model=BankAccountResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_bank_account(
    company_id: int,
    data: BankAccountCreate,
    _=Depends(
        require_company_permission(
            "bank_accounts.manage"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        bank_account = await create_bank_account(
            db,
            company_id=company_id,
            name=data.name,
            account_number=data.account_number,
            currency_code=data.currency_code,
            accounting_account_id=(
                data.accounting_account_id
            ),
        )

        await db.commit()
        await db.refresh(bank_account)

        return bank_account

    except BankAccountError as exc:
        await db.rollback()
        raise _http_error(exc) from exc

    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bank account data conflict",
        ) from exc

    except Exception:
        await db.rollback()
        raise


@router.patch(
    "/{bank_account_id}",
    response_model=BankAccountResponse,
)
async def patch_bank_account(
    company_id: int,
    bank_account_id: int,
    data: BankAccountUpdate,
    _=Depends(
        require_company_permission(
            "bank_accounts.manage"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        values = data.model_dump(
            exclude_unset=True
        )

        bank_account = await update_bank_account(
            db,
            company_id=company_id,
            bank_account_id=bank_account_id,
            **values,
        )

        await db.commit()
        await db.refresh(bank_account)

        return bank_account

    except BankAccountError as exc:
        await db.rollback()
        raise _http_error(exc) from exc

    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bank account data conflict",
        ) from exc

    except Exception:
        await db.rollback()
        raise
