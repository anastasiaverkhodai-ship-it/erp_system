import asyncio

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.company import Company
from app.models.role import Role
from app.models.user import User
from app.models.user_company import user_companies
from app.models.user_company_role import UserCompanyRole


USER_EMAIL = "test@example.com"
ROLE_NAME = "admin"
COMPANY_NAME = "Test Company"


async def seed_test_company():
    async with AsyncSessionLocal() as db:

        # 1. Шукаємо або створюємо компанію
        company_result = await db.execute(
            select(Company).where(
                Company.name == COMPANY_NAME
            )
        )

        company = company_result.scalar_one_or_none()

        if company is None:
            company = Company(
                name=COMPANY_NAME,
                is_active=True,
            )

            db.add(company)
            await db.flush()

            print(
                f"Company created: "
                f"{company.name} (id={company.id})"
            )
        else:
            print(
                f"Company already exists: "
                f"{company.name} (id={company.id})"
            )

        # 2. Шукаємо користувача
        user_result = await db.execute(
            select(User).where(
                User.email == USER_EMAIL
            )
        )

        user = user_result.scalar_one_or_none()

        if user is None:
            print(f"User not found: {USER_EMAIL}")
            await db.rollback()
            return

        # 3. Шукаємо роль admin
        role_result = await db.execute(
            select(Role).where(
                Role.name == ROLE_NAME
            )
        )

        role = role_result.scalar_one_or_none()

        if role is None:
            print(f"Role not found: {ROLE_NAME}")
            await db.rollback()
            return

        # 4. Доступ User ↔ Company
        company_access_result = await db.execute(
            select(user_companies).where(
                user_companies.c.user_id == user.id,
                user_companies.c.company_id == company.id,
            )
        )

        if company_access_result.first() is None:
            await db.execute(
                user_companies.insert().values(
                    user_id=user.id,
                    company_id=company.id,
                )
            )

            print("User → Company access created")

        # 5. Роль користувача саме в цій компанії
        company_role_result = await db.execute(
            select(UserCompanyRole).where(
                UserCompanyRole.user_id == user.id,
                UserCompanyRole.company_id == company.id,
                UserCompanyRole.role_id == role.id,
            )
        )

        company_role = (
            company_role_result.scalar_one_or_none()
        )

        if company_role is None:
            company_role = UserCompanyRole(
                user_id=user.id,
                company_id=company.id,
                role_id=role.id,
                is_active=True,
            )

            db.add(company_role)

            print(
                f"Role '{ROLE_NAME}' assigned "
                f"inside company '{COMPANY_NAME}'"
            )
        else:
            print(
                f"Company role already exists"
            )

        await db.commit()

        print()
        print("Company RBAC seed completed")
        print(f"Company ID: {company.id}")
        print(f"User: {user.email}")
        print(f"Role: {role.name}")


if __name__ == "__main__":
    asyncio.run(seed_test_company())