import asyncio

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.permission import Permission
from app.models.role import Role
from app.models.rbac import role_permissions


ROLE_PERMISSIONS = {
    "admin": [
        "users.read",
        "users.create",
        "users.update",
        "companies.read",
        "companies.update",
        "companies.create",
        "products.read",
        "products.create",
        "products.update",
        "warehouse.read",
        "warehouse.create",
        "warehouse.update",
        "documents.read",
        "documents.create",
        "documents.update",
        "documents.delete",
        "documents.approve",
        "documents.reverse",
        "reports.read",
        "accounting.periods.read",
        "accounting.periods.manage",
        "accounts.read",
        "accounts.create",
        "accounts.update",
        "journal_entries.read",
        "journal_entries.create",
        "journal_entries.update",
        "journal_entries.delete",
        "journal_entries.approve",
        "journal_entries.reverse",

    ],

    "director": [
        "users.read",
        "users.create",
        "users.update",
        "companies.read",
        "companies.update",
        "products.read",
        "products.create",
        "products.update",
        "warehouse.read",
        "warehouse.create",
        "warehouse.update",
        "documents.read",
        "documents.create",
         "documents.update",
        "documents.delete",
        "documents.reverse",
        "documents.approve",
        "reports.read",
        "accounting.periods.read",
        "accounts.read",
        "journal_entries.read",
        "journal_entries.approve",
        "journal_entries.reverse",

    ],

    "accountant": [
        "companies.read",
        "documents.read",
        "documents.create",
        "documents.approve",
        "documents.reverse",
        "reports.read",
        "accounting.periods.read",
        "accounting.periods.manage",   
        "accounts.read",
        "accounts.create",
        "accounts.update", 
        "journal_entries.read",
        "journal_entries.create",
        "journal_entries.update",
        "journal_entries.delete",
        "journal_entries.approve",
        "journal_entries.reverse",
    ],

    "manager": [
        "products.read",
        "products.create",
        "products.update",
        "warehouse.read",
        "warehouse.create",
        "documents.read",
        "documents.create",
        "accounts.read",
    ],

    "seller": [
        "products.read",
        "documents.read",
        "documents.create",
    ],
}


async def seed_role_permissions():
    async with AsyncSessionLocal() as db:

        for role_name, permission_names in ROLE_PERMISSIONS.items():

            role_result = await db.execute(
                select(Role).where(
                    Role.name == role_name
                )
            )

            role = role_result.scalar_one_or_none()

            if role is None:
                print(f"Role not found: {role_name}")
                continue

            for permission_name in permission_names:

                permission_result = await db.execute(
                    select(Permission).where(
                        Permission.name == permission_name
                    )
                )

                permission = (
                    permission_result.scalar_one_or_none()
                )

                if permission is None:
                    print(
                        f"Permission not found: "
                        f"{permission_name}"
                    )
                    continue

                existing_result = await db.execute(
                    select(role_permissions).where(
                        role_permissions.c.role_id == role.id,
                        role_permissions.c.permission_id == permission.id,
                    )
                )

                existing = existing_result.first()

                if existing is None:
                    await db.execute(
                        role_permissions.insert().values(
                            role_id=role.id,
                            permission_id=permission.id,
                        )
                    )

        await db.commit()

        print("Role permissions seeded successfully")


if __name__ == "__main__":
    asyncio.run(seed_role_permissions())