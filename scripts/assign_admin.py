import asyncio

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.role import Role
from app.models.user import User
from app.models.rbac import user_roles


USER_EMAIL = "test@example.com"
ROLE_NAME = "admin"


async def assign_admin():
    async with AsyncSessionLocal() as db:

        user_result = await db.execute(
            select(User).where(
                User.email == USER_EMAIL
            )
        )
        user = user_result.scalar_one_or_none()

        if user is None:
            print(f"User not found: {USER_EMAIL}")
            return

        role_result = await db.execute(
            select(Role).where(
                Role.name == ROLE_NAME
            )
        )
        role = role_result.scalar_one_or_none()

        if role is None:
            print(f"Role not found: {ROLE_NAME}")
            return

        existing_result = await db.execute(
            select(user_roles).where(
                user_roles.c.user_id == user.id,
                user_roles.c.role_id == role.id,
            )
        )

        existing = existing_result.first()

        if existing is not None:
            print(
                f"{USER_EMAIL} already has role {ROLE_NAME}"
            )
            return

        await db.execute(
            user_roles.insert().values(
                user_id=user.id,
                role_id=role.id,
            )
        )

        await db.commit()

        print(
            f"Role {ROLE_NAME} assigned to {USER_EMAIL}"
        )


if __name__ == "__main__":
    asyncio.run(assign_admin())