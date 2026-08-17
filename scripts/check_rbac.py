import asyncio

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.permission import Permission
from app.models.role import Role


async def check_rbac():
    async with AsyncSessionLocal() as db:
        permissions_result = await db.execute(
            select(Permission).order_by(Permission.id)
        )
        permissions = permissions_result.scalars().all()

        roles_result = await db.execute(
            select(Role).order_by(Role.id)
        )
        roles = roles_result.scalars().all()

        print("\nPERMISSIONS:")
        for permission in permissions:
            print(f"{permission.id}: {permission.name}")

        print("\nROLES:")
        for role in roles:
            print(f"{role.id}: {role.name}")


if __name__ == "__main__":
    asyncio.run(check_rbac())