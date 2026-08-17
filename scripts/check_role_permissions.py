import asyncio

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.permission import Permission
from app.models.rbac import role_permissions
from app.models.role import Role


async def check_role_permissions():
    async with AsyncSessionLocal() as db:

        result = await db.execute(
            select(
                Role.name,
                Permission.name,
            )
            .join(
                role_permissions,
                Role.id == role_permissions.c.role_id,
            )
            .join(
                Permission,
                Permission.id == role_permissions.c.permission_id,
            )
            .order_by(
                Role.id,
                Permission.id,
            )
        )

        rows = result.all()

        current_role = None

        for role_name, permission_name in rows:

            if role_name != current_role:
                print(f"\n{role_name.upper()}:")
                current_role = role_name

            print(f"  - {permission_name}")


if __name__ == "__main__":
    asyncio.run(check_role_permissions())