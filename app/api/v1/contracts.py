from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.permissions import (
    require_company_permission,
)
from app.core.database import get_db
from app.models.company import Company
from app.models.contract import Contract
from app.models.counterparty import Counterparty
from app.schemas.contract import (
    ContractCreate,
    ContractResponse,
    ContractUpdate,
)


router = APIRouter(
    prefix="/companies/{company_id}/contracts",
    tags=["Contracts"],
)


async def _get_active_counterparty(
    db: AsyncSession,
    *,
    company_id: int,
    counterparty_id: int,
) -> Counterparty:
    result = await db.execute(
        select(Counterparty).where(
            Counterparty.id == counterparty_id,
            Counterparty.company_id == company_id,
            Counterparty.is_active.is_(True),
        )
    )

    counterparty = result.scalar_one_or_none()

    if counterparty is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Counterparty not found",
        )

    return counterparty


async def _validate_unique_number(
    db: AsyncSession,
    *,
    company_id: int,
    counterparty_id: int,
    number: str,
    exclude_contract_id: int | None = None,
) -> None:
    statement = select(Contract.id).where(
        Contract.company_id == company_id,
        Contract.counterparty_id == counterparty_id,
        Contract.number == number,
    )

    if exclude_contract_id is not None:
        statement = statement.where(
            Contract.id != exclude_contract_id
        )

    existing = (
        await db.execute(statement)
    ).scalar_one_or_none()

    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Contract with this number already "
                "exists for this counterparty"
            ),
        )


@router.get(
    "",
    response_model=list[ContractResponse],
)
async def list_contracts(
    company_id: int,
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "contracts.read"
        )
    ),
):
    result = await db.execute(
        select(Contract)
        .where(
            Contract.company_id == company_id
        )
        .order_by(
            Contract.start_date.desc(),
            Contract.number,
            Contract.id,
        )
    )

    return list(
        result.scalars().all()
    )


@router.get(
    "/{contract_id}",
    response_model=ContractResponse,
)
async def get_contract(
    company_id: int,
    contract_id: int,
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "contracts.read"
        )
    ),
):
    result = await db.execute(
        select(Contract).where(
            Contract.id == contract_id,
            Contract.company_id == company_id,
        )
    )

    contract = result.scalar_one_or_none()

    if contract is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contract not found",
        )

    return contract


@router.post(
    "",
    response_model=ContractResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_contract(
    company_id: int,
    data: ContractCreate,
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "contracts.create"
        )
    ),
):
    company = (
        await db.execute(
            select(Company).where(
                Company.id == company_id,
                Company.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()

    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found",
        )

    await _get_active_counterparty(
        db,
        company_id=company_id,
        counterparty_id=data.counterparty_id,
    )

    await _validate_unique_number(
        db,
        company_id=company_id,
        counterparty_id=data.counterparty_id,
        number=data.number,
    )

    contract = Contract(
        company_id=company_id,
        **data.model_dump(),
    )

    db.add(contract)

    try:
        await db.commit()
        await db.refresh(contract)

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Contract conflicts with "
                "existing data"
            ),
        ) from exc

    return contract


@router.patch(
    "/{contract_id}",
    response_model=ContractResponse,
)
async def update_contract(
    company_id: int,
    contract_id: int,
    data: ContractUpdate,
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "contracts.update"
        )
    ),
):
    result = await db.execute(
        select(Contract)
        .where(
            Contract.id == contract_id,
            Contract.company_id == company_id,
        )
        .with_for_update()
    )

    contract = result.scalar_one_or_none()

    if contract is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contract not found",
        )

    update_data = data.model_dump(
        exclude_unset=True
    )

    non_nullable_fields = {
        "counterparty_id",
        "number",
        "contract_type",
        "status",
        "start_date",
        "currency_code",
        "payment_term_days",
        "credit_limit",
    }

    for field_name in non_nullable_fields:
        if (
            field_name in update_data
            and update_data[field_name] is None
        ):
            raise HTTPException(
                status_code=(
                    status.HTTP_422_UNPROCESSABLE_ENTITY
                ),
                detail=(
                    f"{field_name} cannot be null"
                ),
            )

    new_counterparty_id = update_data.get(
        "counterparty_id",
        contract.counterparty_id,
    )

    new_number = update_data.get(
        "number",
        contract.number,
    )

    if (
        new_counterparty_id
        != contract.counterparty_id
    ):
        await _get_active_counterparty(
            db,
            company_id=company_id,
            counterparty_id=new_counterparty_id,
        )

    if (
        new_counterparty_id
        != contract.counterparty_id
        or new_number != contract.number
    ):
        await _validate_unique_number(
            db,
            company_id=company_id,
            counterparty_id=new_counterparty_id,
            number=new_number,
            exclude_contract_id=contract.id,
        )

    new_start_date = update_data.get(
        "start_date",
        contract.start_date,
    )

    new_end_date = (
        update_data["end_date"]
        if "end_date" in update_data
        else contract.end_date
    )

    if (
        new_end_date is not None
        and new_end_date < new_start_date
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=(
                "end_date cannot be earlier "
                "than start_date"
            ),
        )

    for field_name, value in (
        update_data.items()
    ):
        setattr(
            contract,
            field_name,
            value,
        )

    try:
        await db.commit()
        await db.refresh(contract)

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Contract conflicts with "
                "existing data"
            ),
        ) from exc

    return contract
