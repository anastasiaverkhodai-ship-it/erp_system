import asyncio

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.permission import Permission
from app.models.role import Role


PERMISSIONS = [
    "users.read",
    "users.create",
    "users.update",
    "companies.read",
    "companies.update",
    "companies.create",
    "products.read",
    "products.create",
    "products.update",
    "counterparties.read",
    "counterparties.create",
    "counterparties.update",
    "contracts.read",
    "contracts.create",
    "contracts.update",
    "trade_documents.read",
    "trade_documents.create",
    "trade_documents.update",
    "trade_documents.confirm",
    "trade_documents.cancel",
    "trade_documents.fulfill",
    "warehouse.read",
    "warehouse.create",
    "warehouse.update",
    "documents.read",
    "documents.create",
    "documents.update",
    "documents.approve",
    "documents.delete",
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
    "accounting_rules.read",
    "accounting_rules.create",
    "accounting_rules.update",
]


ROLES = [
    "admin",
    "director",
    "accountant",
    "manager",
    "seller",
]


async def seed_rbac():
    async with AsyncSessionLocal() as db:
        for permission_name in PERMISSIONS:
            result = await db.execute(
                select(Permission).where(
                    Permission.name == permission_name
                )
            )

            permission = result.scalar_one_or_none()

            if permission is None:
                db.add(
                    Permission(
                        name=permission_name
                    )
                )

        for role_name in ROLES:
            result = await db.execute(
                select(Role).where(
                    Role.name == role_name
                )
            )

            role = result.scalar_one_or_none()

            if role is None:
                db.add(
                    Role(
                        name=role_name
                    )
                )

        await db.commit()

        print(
            "RBAC seed completed successfully"
        )


if __name__ == "__main__":
    asyncio.run(seed_rbac())
