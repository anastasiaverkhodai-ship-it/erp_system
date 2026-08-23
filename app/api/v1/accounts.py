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
from app.services.account_hierarchy_service import (
    AccountHierarchyCycleError,
    AccountParentNotFoundError,
    lock_account_hierarchy,
    validate_account_parent,
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
    await lock_account_hierarchy(
        session=db,
        company_id=company_id,
    )

    existing_result = await db.execute(
        select(Account).where(
            Account.company_id == company_id,
            Account.code == data.code,
        )
    )

    if (
        existing_result.scalar_one_or_none()
        is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Account with this code already exists"
            ),
        )

    try:
        parent = await validate_account_parent(
            session=db,
            company_id=company_id,
            account_id=None,
            parent_id=data.parent_id,
        )

    except AccountParentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except AccountHierarchyCycleError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    account = Account(
        company_id=company_id,
        code=data.code,
        name=data.name,
        account_type=data.account_type,
        normal_balance=data.normal_balance,
        parent_id=data.parent_id,
        is_postable=data.is_postable,
        is_system=False,
        is_active=True,
    )

    if parent is not None:
        parent.is_postable = False

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
    update_data = data.model_dump(
        exclude_unset=True
    )

    hierarchy_sensitive = (
        "parent_id" in update_data
        or "is_postable" in update_data
    )

    if hierarchy_sensitive:
        await lock_account_hierarchy(
            session=db,
            company_id=company_id,
        )

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

    if account.is_system:
        protected_system_fields = {
            "code",
            "account_type",
            "normal_balance",
            "parent_id",
            "is_postable",
        }

        attempted_protected_fields = (
            protected_system_fields
            & set(update_data)
        )

        if attempted_protected_fields:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "System account fields cannot be "
                    "changed through the regular API: "
                    f"{sorted(attempted_protected_fields)}"
                ),
            )

    if "code" in update_data:
        duplicate_result = await db.execute(
            select(Account).where(
                Account.company_id == company_id,
                Account.code == update_data["code"],
                Account.id != account_id,
            )
        )

        if (
            duplicate_result.scalar_one_or_none()
            is not None
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Account with this code "
                    "already exists"
                ),
            )

    new_parent = None

    if "parent_id" in update_data:
        try:
            new_parent = await validate_account_parent(
                session=db,
                company_id=company_id,
                account_id=account_id,
                parent_id=update_data["parent_id"],
            )

        except AccountParentNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

        except AccountHierarchyCycleError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    if update_data.get("is_postable") is True:
        child_result = await db.execute(
            select(Account.id)
            .where(
                Account.company_id == company_id,
                Account.parent_id == account_id,
            )
            .limit(1)
        )

        if child_result.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Account with child accounts "
                    "cannot be postable"
                ),
            )

    if new_parent is not None:
        new_parent.is_postable = False

    for field, value in update_data.items():
        setattr(
            account,
            field,
            value,
        )

    await db.commit()
    await db.refresh(account)

    return account
