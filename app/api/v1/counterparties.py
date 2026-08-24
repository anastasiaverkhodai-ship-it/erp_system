from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.company import Company
from app.models.counterparty import Counterparty
from app.schemas.counterparty import (
    CounterpartyCreate,
    CounterpartyResponse,
    CounterpartyUpdate,
)


router = APIRouter(
    prefix="/companies/{company_id}/counterparties",
    tags=["Counterparties"],
)


IDENTIFIER_FIELDS = {
    "edrpou": "EDRPOU",
    "tax_number": "Tax number",
    "vat_number": "VAT number",
}


async def _find_duplicate_identifier(
    *,
    db: AsyncSession,
    company_id: int,
    field_name: str,
    value: str | None,
    exclude_counterparty_id: int | None = None,
) -> int | None:
    if value is None:
        return None

    column = getattr(
        Counterparty,
        field_name,
    )

    conditions = [
        Counterparty.company_id == company_id,
        column == value,
    ]

    if exclude_counterparty_id is not None:
        conditions.append(
            Counterparty.id
            != exclude_counterparty_id
        )

    result = await db.execute(
        select(Counterparty.id)
        .where(*conditions)
        .limit(1)
    )

    return result.scalar_one_or_none()


async def _validate_unique_identifiers(
    *,
    db: AsyncSession,
    company_id: int,
    values: dict,
    exclude_counterparty_id: int | None = None,
) -> None:
    for field_name, label in (
        IDENTIFIER_FIELDS.items()
    ):
        if field_name not in values:
            continue

        value = values[field_name]

        duplicate_id = (
            await _find_duplicate_identifier(
                db=db,
                company_id=company_id,
                field_name=field_name,
                value=value,
                exclude_counterparty_id=(
                    exclude_counterparty_id
                ),
            )
        )

        if duplicate_id is not None:
            raise HTTPException(
                status_code=(
                    status.HTTP_409_CONFLICT
                ),
                detail=(
                    f"{label} already exists "
                    "for another counterparty "
                    "in this company"
                ),
            )


# ---------------------------------------------------------
# GET COUNTERPARTY LIST
# ---------------------------------------------------------


@router.get(
    "",
    response_model=list[CounterpartyResponse],
)
async def get_counterparties(
    company_id: int,
    _=Depends(
        require_company_permission(
            "counterparties.read"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Counterparty)
        .where(
            Counterparty.company_id
            == company_id
        )
        .order_by(
            Counterparty.name.asc(),
            Counterparty.id.asc(),
        )
    )

    return result.scalars().all()


# ---------------------------------------------------------
# GET ONE COUNTERPARTY
# ---------------------------------------------------------


@router.get(
    "/{counterparty_id}",
    response_model=CounterpartyResponse,
)
async def get_counterparty(
    company_id: int,
    counterparty_id: int,
    _=Depends(
        require_company_permission(
            "counterparties.read"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Counterparty).where(
            Counterparty.id
            == counterparty_id,
            Counterparty.company_id
            == company_id,
        )
    )

    counterparty = (
        result.scalar_one_or_none()
    )

    if counterparty is None:
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail="Counterparty not found",
        )

    return counterparty


# ---------------------------------------------------------
# CREATE COUNTERPARTY
# ---------------------------------------------------------


@router.post(
    "",
    response_model=CounterpartyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_counterparty(
    company_id: int,
    data: CounterpartyCreate,
    _=Depends(
        require_company_permission(
            "counterparties.create"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        company_result = await db.execute(
            select(Company).where(
                Company.id == company_id,
                Company.is_active.is_(True),
            )
        )

        company = (
            company_result.scalar_one_or_none()
        )

        if company is None:
            raise HTTPException(
                status_code=(
                    status.HTTP_404_NOT_FOUND
                ),
                detail="Company not found",
            )

        create_data = data.model_dump()

        await _validate_unique_identifiers(
            db=db,
            company_id=company_id,
            values=create_data,
        )

        counterparty = Counterparty(
            company_id=company_id,
            **create_data,
            is_active=True,
        )

        db.add(counterparty)

        await db.commit()
        await db.refresh(counterparty)

        return counterparty

    except HTTPException:
        await db.rollback()
        raise

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT
            ),
            detail=(
                "Counterparty could not be "
                "created because of a "
                "data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise


# ---------------------------------------------------------
# UPDATE COUNTERPARTY
# ---------------------------------------------------------


@router.patch(
    "/{counterparty_id}",
    response_model=CounterpartyResponse,
)
async def update_counterparty(
    company_id: int,
    counterparty_id: int,
    data: CounterpartyUpdate,
    _=Depends(
        require_company_permission(
            "counterparties.update"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await db.execute(
            select(Counterparty)
            .where(
                Counterparty.id
                == counterparty_id,
                Counterparty.company_id
                == company_id,
            )
            .with_for_update()
        )

        counterparty = (
            result.scalar_one_or_none()
        )

        if counterparty is None:
            raise HTTPException(
                status_code=(
                    status.HTTP_404_NOT_FOUND
                ),
                detail="Counterparty not found",
            )

        update_data = data.model_dump(
            exclude_unset=True
        )

        non_nullable_fields = {
            "name",
            "counterparty_type",
            "vat_status",
            "default_currency_code",
            "payment_term_days",
            "credit_limit",
            "is_active",
        }

        invalid_null_fields = sorted(
            field
            for field in non_nullable_fields
            if (
                field in update_data
                and update_data[field] is None
            )
        )

        if invalid_null_fields:
            raise HTTPException(
                status_code=(
                    status.HTTP_422_UNPROCESSABLE_ENTITY
                ),
                detail=(
                    "Fields cannot be null: "
                    + ", ".join(
                        invalid_null_fields
                    )
                ),
            )

        identifiers_to_check = {
            field_name: update_data[
                field_name
            ]
            for field_name in IDENTIFIER_FIELDS
            if (
                field_name in update_data
                and update_data[field_name]
                != getattr(
                    counterparty,
                    field_name,
                )
            )
        }

        await _validate_unique_identifiers(
            db=db,
            company_id=company_id,
            values=identifiers_to_check,
            exclude_counterparty_id=(
                counterparty.id
            ),
        )

        for field, value in (
            update_data.items()
        ):
            setattr(
                counterparty,
                field,
                value,
            )

        await db.commit()
        await db.refresh(counterparty)

        return counterparty

    except HTTPException:
        await db.rollback()
        raise

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT
            ),
            detail=(
                "Counterparty could not be "
                "updated because of a "
                "data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise
