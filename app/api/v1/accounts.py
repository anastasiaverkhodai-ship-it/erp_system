from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.account import Account
from app.models.user import User
from app.schemas.account import (
    AccountCreate,
    AccountResponse,
    AccountUpdate,
)


router = APIRouter(
    prefix="/companies/{company_id}/accounts",
    tags=["Chart of Accounts"],
)


@router.get(
    "/",
    response_model=list[AccountResponse],
)
async def get_accounts(
    company_id: int,
    current_user: User = Depends(
        require_company_permission("accounts.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Account)
        .where(
            Account.company_id == company_id
        )
        .order_by(Account.code)
    )

    return result.scalars().all()


@router.post(
    "/",
    response_model=AccountResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_account(
    company_id: int,
    data: AccountCreate,
    current_user: User = Depends(
        require_company_permission("accounts.create")
    ),
    db: AsyncSession = Depends(get_db),
):
    existing_result = await db.execute(
        select(Account).where(
            Account.company_id == company_id,
            Account.code == data.code,
        )
    )

    if existing_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account with this code already exists",
        )

    if data.parent_id is not None:
        parent_result = await db.execute(
            select(Account).where(
                Account.id == data.parent_id,
                Account.company_id == company_id,
            )
        )

        if parent_result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Parent account not found",
            )

    account = Account(
        company_id=company_id,
        code=data.code,
        name=data.name,
        account_type=data.account_type,
        parent_id=data.parent_id,
        is_active=True,
    )

    db.add(account)

    await db.commit()
    await db.refresh(account)

    return account


@router.patch(
    "/{account_id}",
    response_model=AccountResponse,
)
async def update_account(
    company_id: int,
    account_id: int,
    data: AccountUpdate,
    current_user: User = Depends(
        require_company_permission("accounts.update")
    ),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Account).where(
            Account.id == account_id,
            Account.company_id == company_id,
        )
    )

    account = result.scalar_one_or_none()

    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
        )

    update_data = data.model_dump(
        exclude_unset=True
    )

    if "code" in update_data:
        duplicate_result = await db.execute(
            select(Account).where(
                Account.company_id == company_id,
                Account.code == update_data["code"],
                Account.id != account_id,
            )
        )

        if duplicate_result.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Account with this code already exists",
            )

    if (
        "parent_id" in update_data
        and update_data["parent_id"] is not None
    ):
        if update_data["parent_id"] == account_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Account cannot be its own parent",
            )

        parent_result = await db.execute(
            select(Account).where(
                Account.id == update_data["parent_id"],
                Account.company_id == company_id,
            )
        )

        if parent_result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Parent account not found",
            )

    for field, value in update_data.items():
        setattr(account, field, value)

    await db.commit()
    await db.refresh(account)

    return account