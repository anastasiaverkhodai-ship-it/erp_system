from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.permission import Permission
from app.models.rbac import (
    role_permissions,
    user_roles,
)
from app.models.user import User
from app.models.user_company_role import UserCompanyRole


def require_global_permission(permission_name: str):
    """
    Перевіряє системний permission користувача.

    Використовується для операцій, які не належать
    конкретній компанії, наприклад створення компанії.
    """

    async def permission_checker(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:

        result = await db.execute(
            select(Permission.id)
            .join(
                role_permissions,
                Permission.id
                == role_permissions.c.permission_id,
            )
            .join(
                user_roles,
                user_roles.c.role_id
                == role_permissions.c.role_id,
            )
            .where(
                user_roles.c.user_id == current_user.id,
                Permission.name == permission_name,
            )
            .limit(1)
        )

        permission_id = result.scalar_one_or_none()

        if permission_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Global permission denied",
            )

        return current_user

    return permission_checker


def require_company_permission(permission_name: str):
    """
    Перевіряє permission користувача
    в межах конкретної компанії.
    """

    async def permission_checker(
        company_id: int,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:

        result = await db.execute(
            select(Permission.id)
            .join(
                role_permissions,
                Permission.id
                == role_permissions.c.permission_id,
            )
            .join(
                UserCompanyRole,
                UserCompanyRole.role_id
                == role_permissions.c.role_id,
            )
            .where(
                UserCompanyRole.user_id
                == current_user.id,

                UserCompanyRole.company_id
                == company_id,

                UserCompanyRole.is_active.is_(True),

                Permission.name
                == permission_name,
            )
            .limit(1)
        )

        permission_id = result.scalar_one_or_none()

        if permission_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied for this company",
            )

        return current_user

    return permission_checker