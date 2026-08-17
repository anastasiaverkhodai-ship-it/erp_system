from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.role import Role
from app.models.user import User
from app.models.user_company import user_companies
from app.models.user_company_role import UserCompanyRole
from app.schemas.company_user import CompanyUserResponse

from app.schemas.company_user import (
    CompanyUserAdd,
    CompanyUserResponse,
     CompanyUserRoleUpdate,
)


router = APIRouter(
    prefix="/companies/{company_id}/users",
    tags=["Company Users"],
)


@router.get(
    "/",
    response_model=list[CompanyUserResponse],
)
async def get_company_users(
    company_id: int,
    current_user: User = Depends(
        require_company_permission("users.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    # Отримуємо всіх користувачів компанії
    users_result = await db.execute(
        select(User)
        .join(
            user_companies,
            User.id == user_companies.c.user_id,
        )
        .where(
            user_companies.c.company_id == company_id
        )
        .order_by(
            User.last_name,
            User.first_name,
        )
    )

    users = users_result.scalars().all()

    response = []

    for user in users:

        # Отримуємо активні ролі користувача
        # саме в цій компанії
        roles_result = await db.execute(
            select(Role.name)
            .join(
                UserCompanyRole,
                Role.id == UserCompanyRole.role_id,
            )
            .where(
                UserCompanyRole.user_id == user.id,
                UserCompanyRole.company_id == company_id,
                UserCompanyRole.is_active.is_(True),
            )
            .order_by(Role.name)
        )

        roles = list(
            roles_result.scalars().all()
        )

        response.append(
            CompanyUserResponse(
                id=user.id,
                email=user.email,
                first_name=user.first_name,
                last_name=user.last_name,
                is_active=user.is_active,
                roles=roles,
            )
        )

    return response

@router.post(
    "/",
    response_model=CompanyUserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_company_user(
    company_id: int,
    data: CompanyUserAdd,
    current_user: User = Depends(
        require_company_permission("users.create")
    ),
    db: AsyncSession = Depends(get_db),
):
    # 1. Шукаємо користувача за email
    user_result = await db.execute(
        select(User).where(
            User.email == data.email
        )
    )

    user = user_result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # 2. Шукаємо роль
    role_result = await db.execute(
        select(Role).where(
            Role.name == data.role
        )
    )

    role = role_result.scalar_one_or_none()

    if role is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found",
        )

    # 3. Перевіряємо доступ User → Company
    access_result = await db.execute(
        select(user_companies).where(
            user_companies.c.user_id == user.id,
            user_companies.c.company_id == company_id,
        )
    )

    access = access_result.first()

    if access is None:
        await db.execute(
            user_companies.insert().values(
                user_id=user.id,
                company_id=company_id,
            )
        )

    # 4. Перевіряємо роль у цій компанії
    role_access_result = await db.execute(
        select(UserCompanyRole).where(
            UserCompanyRole.user_id == user.id,
            UserCompanyRole.company_id == company_id,
            UserCompanyRole.role_id == role.id,
        )
    )

    existing_role = role_access_result.scalar_one_or_none()

    if existing_role is None:
        db.add(
            UserCompanyRole(
                user_id=user.id,
                company_id=company_id,
                role_id=role.id,
                is_active=True,
            )
        )
    else:
        existing_role.is_active = True

    await db.commit()

    # 5. Отримуємо всі активні ролі користувача
    roles_result = await db.execute(
        select(Role.name)
        .join(
            UserCompanyRole,
            Role.id == UserCompanyRole.role_id,
        )
        .where(
            UserCompanyRole.user_id == user.id,
            UserCompanyRole.company_id == company_id,
            UserCompanyRole.is_active.is_(True),
        )
        .order_by(Role.name)
    )

    roles = list(
        roles_result.scalars().all()
    )

    return CompanyUserResponse(
        id=user.id,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        is_active=user.is_active,
        roles=roles,
    )
@router.patch(
    "/{user_id}/role",
    response_model=CompanyUserResponse,
)
async def update_company_user_role(
    company_id: int,
    user_id: int,
    data: CompanyUserRoleUpdate,
    current_user: User = Depends(
        require_company_permission("users.update")
    ),
    db: AsyncSession = Depends(get_db),
):
    user_result = await db.execute(
        select(User).where(
            User.id == user_id
        )
    )

    user = user_result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    role_result = await db.execute(
        select(Role).where(
            Role.name == data.role
        )
    )

    role = role_result.scalar_one_or_none()

    if role is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found",
        )

    existing_roles_result = await db.execute(
        select(UserCompanyRole).where(
            UserCompanyRole.user_id == user_id,
            UserCompanyRole.company_id == company_id,
        )
    )

    existing_roles = (
        existing_roles_result.scalars().all()
    )

    if not existing_roles:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User is not assigned to this company",
        )

    for existing_role in existing_roles:
        existing_role.is_active = False

    target_role_result = await db.execute(
        select(UserCompanyRole).where(
            UserCompanyRole.user_id == user_id,
            UserCompanyRole.company_id == company_id,
            UserCompanyRole.role_id == role.id,
        )
    )

    target_role = target_role_result.scalar_one_or_none()

    if target_role is None:
        db.add(
            UserCompanyRole(
                user_id=user_id,
                company_id=company_id,
                role_id=role.id,
                is_active=True,
            )
        )
    else:
        target_role.is_active = True

    await db.commit()

    roles_result = await db.execute(
        select(Role.name)
        .join(
            UserCompanyRole,
            Role.id == UserCompanyRole.role_id,
        )
        .where(
            UserCompanyRole.user_id == user_id,
            UserCompanyRole.company_id == company_id,
            UserCompanyRole.is_active.is_(True),
        )
    )

    roles = list(
        roles_result.scalars().all()
    )

    return CompanyUserResponse(
        id=user.id,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        is_active=user.is_active,
        roles=roles,
    )

@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def deactivate_company_user(
    company_id: int,
    user_id: int,
    current_user: User = Depends(
        require_company_permission("users.update")
    ),
    db: AsyncSession = Depends(get_db),
):
    roles_result = await db.execute(
        select(UserCompanyRole).where(
            UserCompanyRole.user_id == user_id,
            UserCompanyRole.company_id == company_id,
            UserCompanyRole.is_active.is_(True),
        )
    )

    roles = roles_result.scalars().all()

    if not roles:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active company access not found",
        )

    for role in roles:
        role.is_active = False

    await db.execute(
        user_companies.delete().where(
            user_companies.c.user_id == user_id,
            user_companies.c.company_id == company_id,
        )
    )

    await db.commit()

    return None