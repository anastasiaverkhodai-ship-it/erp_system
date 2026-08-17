from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.permissions import (
    require_company_permission,
    require_global_permission,
)
from app.core.database import get_db
from app.models.company import Company
from app.models.role import Role
from app.models.user import User
from app.models.user_company import user_companies
from app.models.user_company_role import UserCompanyRole
from app.schemas.company import (
    CompanyCreate,
    CompanyResponse,
    CompanyUpdate,
)


router = APIRouter(
    prefix="/companies",
    tags=["Companies"],
)


# ---------------------------------------------------------
# GET /companies
# Показує компанії, доступні поточному користувачу
# ---------------------------------------------------------

@router.get(
    "/",
    response_model=list[CompanyResponse],
)
async def get_companies(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Company)
        .join(
            user_companies,
            Company.id == user_companies.c.company_id,
        )
        .where(
            user_companies.c.user_id == current_user.id,
            Company.is_active.is_(True),
        )
        .order_by(Company.name)
    )

    return result.scalars().all()


# ---------------------------------------------------------
# POST /companies
# Створення нової компанії
# Потребує GLOBAL permission companies.create
# ---------------------------------------------------------

@router.post(
    "/",
    response_model=CompanyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_company(
    data: CompanyCreate,
    current_user: User = Depends(
        require_global_permission("companies.create")
    ),
    db: AsyncSession = Depends(get_db),
):
    # Перевіряємо ЄДРПОУ
    if data.edrpou is not None:
        result = await db.execute(
            select(Company).where(
                Company.edrpou == data.edrpou
            )
        )

        if result.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Company with this EDRPOU already exists",
            )

    # Перевіряємо VAT number
    if data.vat_number is not None:
        result = await db.execute(
            select(Company).where(
                Company.vat_number == data.vat_number
            )
        )

        if result.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Company with this VAT number already exists",
            )

    # Знаходимо роль admin
    role_result = await db.execute(
        select(Role).where(
            Role.name == "admin"
        )
    )

    admin_role = role_result.scalar_one_or_none()

    if admin_role is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="System role 'admin' is missing",
        )

    # Створюємо компанію
    company = Company(
        name=data.name,
        edrpou=data.edrpou,
        vat_number=data.vat_number,
        is_active=True,
    )

    db.add(company)

    # Потрібен company.id до commit
    await db.flush()

    # Даємо користувачу доступ до компанії
    await db.execute(
        user_companies.insert().values(
            user_id=current_user.id,
            company_id=company.id,
        )
    )

    # Призначаємо creator роль admin
    db.add(
        UserCompanyRole(
            user_id=current_user.id,
            company_id=company.id,
            role_id=admin_role.id,
            is_active=True,
        )
    )

    await db.commit()
    await db.refresh(company)

    return company


# ---------------------------------------------------------
# GET /companies/{company_id}
# Перегляд конкретної компанії
# ---------------------------------------------------------

@router.get(
    "/{company_id}",
    response_model=CompanyResponse,
)
async def get_company(
    company_id: int,
    current_user: User = Depends(
        require_company_permission("companies.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Company).where(
            Company.id == company_id,
            Company.is_active.is_(True),
        )
    )

    company = result.scalar_one_or_none()

    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found",
        )

    return company


# ---------------------------------------------------------
# PATCH /companies/{company_id}
# Редагування компанії
# ---------------------------------------------------------

@router.patch(
    "/{company_id}",
    response_model=CompanyResponse,
)
async def update_company(
    company_id: int,
    data: CompanyUpdate,
    current_user: User = Depends(
        require_company_permission("companies.update")
    ),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Company).where(
            Company.id == company_id
        )
    )

    company = result.scalar_one_or_none()

    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found",
        )

    update_data = data.model_dump(
        exclude_unset=True
    )

    for field, value in update_data.items():
        setattr(company, field, value)

    await db.commit()
    await db.refresh(company)

    return company