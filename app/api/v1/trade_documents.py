from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.company import Company
from app.models.contract import Contract
from app.models.counterparty import Counterparty
from app.models.product import Product
from app.models.trade_document import TradeDocument
from app.models.trade_document_line import TradeDocumentLine
from app.models.user import User
from app.models.warehouse import Warehouse
from app.schemas.trade_document import (
    TradeDocumentCreate,
    TradeDocumentResponse,
    TradeDocumentUpdate,
)
from app.services.trade_document_types import (
    TradeDocumentStatus,
)
from app.services.trade_document_validation import (
    TradeDocumentValidationError,
    validate_trade_document_contract,
)


from app.services.reservation_persistence_service import (
    ReservationPersistenceError,
)
from app.services.trade_document_lifecycle_service import (
    SalesOrderLinesRequiredError,
    SalesOrderNotFoundError,
    SalesOrderReferenceError,
    SalesOrderReservationStateError,
    SalesOrderStatusError,
    SalesOrderTypeError,
    SalesOrderWarehouseRequiredError,
    cancel_sales_order,
    confirm_sales_order,
)


router = APIRouter(
    prefix="/companies/{company_id}/trade-documents",
    tags=["Trade Documents"],
)


async def _get_active_company(
    db: AsyncSession,
    *,
    company_id: int,
) -> Company:
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

    return company


async def _get_active_counterparty(
    db: AsyncSession,
    *,
    company_id: int,
    counterparty_id: int,
) -> Counterparty:
    counterparty = (
        await db.execute(
            select(Counterparty).where(
                Counterparty.id == counterparty_id,
                Counterparty.company_id == company_id,
                Counterparty.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()

    if counterparty is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Counterparty not found",
        )

    return counterparty


async def _validate_contract(
    db: AsyncSession,
    *,
    company_id: int,
    counterparty_id: int,
    contract_id: int | None,
    direction,
    document_date,
    currency_code: str,
) -> Contract | None:
    if contract_id is None:
        return None

    contract = (
        await db.execute(
            select(Contract).where(
                Contract.id == contract_id,
                Contract.company_id == company_id,
            )
        )
    ).scalar_one_or_none()

    if contract is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contract not found",
        )

    try:
        validate_trade_document_contract(
            contract=contract,
            company_id=company_id,
            counterparty_id=counterparty_id,
            direction=direction,
            document_date=document_date,
            currency_code=currency_code,
        )
    except TradeDocumentValidationError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=str(exc),
        ) from exc

    return contract


async def _validate_lines(
    db: AsyncSession,
    *,
    company_id: int,
    lines,
) -> None:
    product_ids = {
        line.product_id
        for line in lines
    }

    valid_product_ids = set(
        (
            await db.execute(
                select(Product.id).where(
                    Product.company_id == company_id,
                    Product.id.in_(product_ids),
                    Product.is_active.is_(True),
                )
            )
        ).scalars().all()
    )

    invalid_product_ids = (
        product_ids - valid_product_ids
    )

    if invalid_product_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Invalid or foreign company products: "
                + ", ".join(
                    str(item)
                    for item in sorted(
                        invalid_product_ids
                    )
                )
            ),
        )

    warehouse_ids = {
        line.warehouse_id
        for line in lines
        if line.warehouse_id is not None
    }

    if not warehouse_ids:
        return

    valid_warehouse_ids = set(
        (
            await db.execute(
                select(Warehouse.id).where(
                    Warehouse.company_id == company_id,
                    Warehouse.id.in_(warehouse_ids),
                    Warehouse.is_active.is_(True),
                )
            )
        ).scalars().all()
    )

    invalid_warehouse_ids = (
        warehouse_ids - valid_warehouse_ids
    )

    if invalid_warehouse_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Invalid or foreign company warehouses: "
                + ", ".join(
                    str(item)
                    for item in sorted(
                        invalid_warehouse_ids
                    )
                )
            ),
        )


async def _validate_unique_number(
    db: AsyncSession,
    *,
    company_id: int,
    direction,
    kind,
    number: str,
    exclude_document_id: int | None = None,
) -> None:
    statement = select(
        TradeDocument.id
    ).where(
        TradeDocument.company_id == company_id,
        TradeDocument.direction == direction,
        TradeDocument.kind == kind,
        TradeDocument.number == number,
    )

    if exclude_document_id is not None:
        statement = statement.where(
            TradeDocument.id
            != exclude_document_id
        )

    existing = (
        await db.execute(statement)
    ).scalar_one_or_none()

    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Trade document number already exists "
                "for this direction and kind"
            ),
        )


async def _load_trade_document(
    db: AsyncSession,
    *,
    company_id: int,
    document_id: int,
) -> TradeDocument:
    document = (
        await db.execute(
            select(TradeDocument)
            .options(
                selectinload(
                    TradeDocument.lines
                )
            )
            .where(
                TradeDocument.id == document_id,
                TradeDocument.company_id
                == company_id,
            )
        )
    ).scalar_one_or_none()

    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trade document not found",
        )

    return document


@router.get(
    "",
    response_model=list[TradeDocumentResponse],
)
async def list_trade_documents(
    company_id: int,
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "trade_documents.read"
        )
    ),
):
    result = await db.execute(
        select(TradeDocument)
        .options(
            selectinload(
                TradeDocument.lines
            )
        )
        .where(
            TradeDocument.company_id
            == company_id
        )
        .order_by(
            TradeDocument.document_date.desc(),
            TradeDocument.id.desc(),
        )
    )

    return list(
        result.scalars().all()
    )


@router.get(
    "/{document_id}",
    response_model=TradeDocumentResponse,
)
async def get_trade_document(
    company_id: int,
    document_id: int,
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "trade_documents.read"
        )
    ),
):
    return await _load_trade_document(
        db,
        company_id=company_id,
        document_id=document_id,
    )


@router.post(
    "",
    response_model=TradeDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_trade_document(
    company_id: int,
    data: TradeDocumentCreate,
    current_user: User = Depends(
        get_current_user
    ),
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "trade_documents.create"
        )
    ),
):
    try:
        await _get_active_company(
            db,
            company_id=company_id,
        )

        await _get_active_counterparty(
            db,
            company_id=company_id,
            counterparty_id=(
                data.counterparty_id
            ),
        )

        await _validate_contract(
            db,
            company_id=company_id,
            counterparty_id=(
                data.counterparty_id
            ),
            contract_id=data.contract_id,
            direction=data.direction,
            document_date=data.document_date,
            currency_code=data.currency_code,
        )

        await _validate_lines(
            db,
            company_id=company_id,
            lines=data.lines,
        )

        await _validate_unique_number(
            db,
            company_id=company_id,
            direction=data.direction,
            kind=data.kind,
            number=data.number,
        )

        header_data = data.model_dump(
            exclude={
                "lines",
            }
        )

        document = TradeDocument(
            company_id=company_id,
            status=TradeDocumentStatus.DRAFT,
            created_by=current_user.id,
            **header_data,
        )

        db.add(document)

        await db.flush()

        for line_number, line_data in enumerate(
            data.lines,
            start=1,
        ):
            db.add(
                TradeDocumentLine(
                    company_id=company_id,
                    trade_document_id=document.id,
                    line_number=line_number,
                    product_id=line_data.product_id,
                    warehouse_id=(
                        line_data.warehouse_id
                    ),
                    quantity=line_data.quantity,
                    unit_price=line_data.unit_price,
                )
            )

        await db.commit()

        return await _load_trade_document(
            db,
            company_id=company_id,
            document_id=document.id,
        )

    except HTTPException:
        await db.rollback()
        raise

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Trade document could not be "
                "created because of a data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise


@router.patch(
    "/{document_id}",
    response_model=TradeDocumentResponse,
)
async def update_trade_document(
    company_id: int,
    document_id: int,
    data: TradeDocumentUpdate,
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "trade_documents.update"
        )
    ),
):
    try:
        await _get_active_company(
            db,
            company_id=company_id,
        )

        result = await db.execute(
            select(TradeDocument)
            .where(
                TradeDocument.id == document_id,
                TradeDocument.company_id
                == company_id,
            )
            .with_for_update()
        )

        document = (
            result.scalar_one_or_none()
        )

        if document is None:
            raise HTTPException(
                status_code=(
                    status.HTTP_404_NOT_FOUND
                ),
                detail="Trade document not found",
            )

        if (
            document.status
            != TradeDocumentStatus.DRAFT
        ):
            raise HTTPException(
                status_code=(
                    status.HTTP_409_CONFLICT
                ),
                detail=(
                    "Only draft trade documents "
                    "can be edited"
                ),
            )

        update_data = data.model_dump(
            exclude_unset=True
        )

        non_nullable_fields = {
            "number",
            "direction",
            "kind",
            "document_date",
            "counterparty_id",
            "currency_code",
            "payment_term_days",
            "lines",
        }

        invalid_null_fields = sorted(
            field_name
            for field_name
            in non_nullable_fields
            if (
                field_name in update_data
                and update_data[field_name]
                is None
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

        new_number = update_data.get(
            "number",
            document.number,
        )

        new_direction = update_data.get(
            "direction",
            document.direction,
        )

        new_kind = update_data.get(
            "kind",
            document.kind,
        )

        new_document_date = update_data.get(
            "document_date",
            document.document_date,
        )

        new_counterparty_id = (
            update_data.get(
                "counterparty_id",
                document.counterparty_id,
            )
        )

        new_contract_id = (
            update_data["contract_id"]
            if "contract_id" in update_data
            else document.contract_id
        )

        new_currency_code = update_data.get(
            "currency_code",
            document.currency_code,
        )

        await _get_active_counterparty(
            db,
            company_id=company_id,
            counterparty_id=(
                new_counterparty_id
            ),
        )

        await _validate_contract(
            db,
            company_id=company_id,
            counterparty_id=(
                new_counterparty_id
            ),
            contract_id=new_contract_id,
            direction=new_direction,
            document_date=new_document_date,
            currency_code=new_currency_code,
        )

        await _validate_unique_number(
            db,
            company_id=company_id,
            direction=new_direction,
            kind=new_kind,
            number=new_number,
            exclude_document_id=(
                document.id
            ),
        )

        if "lines" in update_data:
            if data.lines is None:
                raise HTTPException(
                    status_code=(
                        status.HTTP_422_UNPROCESSABLE_ENTITY
                    ),
                    detail=(
                        "Trade document lines "
                        "cannot be null"
                    ),
                )

            await _validate_lines(
                db,
                company_id=company_id,
                lines=data.lines,
            )

        header_fields = {
            "number",
            "direction",
            "kind",
            "document_date",
            "counterparty_id",
            "contract_id",
            "currency_code",
            "payment_term_days",
        }

        for field_name in header_fields:
            if field_name in update_data:
                setattr(
                    document,
                    field_name,
                    update_data[field_name],
                )

        if "lines" in update_data:
            await db.execute(
                delete(
                    TradeDocumentLine
                ).where(
                    TradeDocumentLine
                    .trade_document_id
                    == document.id
                )
            )

            for (
                line_number,
                line_data,
            ) in enumerate(
                data.lines,
                start=1,
            ):
                db.add(
                    TradeDocumentLine(
                        company_id=company_id,
                        trade_document_id=(
                            document.id
                        ),
                        line_number=line_number,
                        product_id=(
                            line_data.product_id
                        ),
                        warehouse_id=(
                            line_data.warehouse_id
                        ),
                        quantity=(
                            line_data.quantity
                        ),
                        unit_price=(
                            line_data.unit_price
                        ),
                    )
                )

        await db.commit()

        return await _load_trade_document(
            db,
            company_id=company_id,
            document_id=document.id,
        )

    except HTTPException:
        await db.rollback()
        raise

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Trade document could not be "
                "updated because of a data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise


@router.post(
    "/{document_id}/confirm",
    response_model=TradeDocumentResponse,
)
async def confirm_trade_document_sales_order(
    company_id: int,
    document_id: int,
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "trade_documents.confirm"
        )
    ),
):
    """
    Confirm a draft Sales Order atomically.
    """

    try:
        document = await confirm_sales_order(
            db,
            company_id=company_id,
            document_id=document_id,
        )

        document_id_value = document.id

        await db.commit()

    except SalesOrderNotFoundError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except SalesOrderStatusError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except (
        SalesOrderTypeError,
        SalesOrderLinesRequiredError,
        SalesOrderWarehouseRequiredError,
        SalesOrderReferenceError,
    ) as exc:
        await db.rollback()

        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=str(exc),
        ) from exc

    except ReservationPersistenceError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Sales order could not be confirmed "
                "because of a data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise

    return await _load_trade_document(
        db,
        company_id=company_id,
        document_id=document_id_value,
    )


@router.post(
    "/{document_id}/cancel",
    response_model=TradeDocumentResponse,
)
async def cancel_trade_document_sales_order(
    company_id: int,
    document_id: int,
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "trade_documents.cancel"
        )
    ),
):
    """
    Cancel a Sales Order atomically.

    DRAFT:
        CANCELLED without reservation movements.

    CONFIRMED:
        RELEASE all outstanding reservations,
        then CANCELLED.

    Partially fulfilled / fulfilled orders are rejected.
    """

    try:
        document = await cancel_sales_order(
            db,
            company_id=company_id,
            document_id=document_id,
        )

        document_id_value = document.id

        await db.commit()

    except SalesOrderNotFoundError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except (
        SalesOrderStatusError,
        SalesOrderReservationStateError,
        ReservationPersistenceError,
    ) as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except (
        SalesOrderTypeError,
        SalesOrderLinesRequiredError,
        SalesOrderWarehouseRequiredError,
    ) as exc:
        await db.rollback()

        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=str(exc),
        ) from exc

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Sales order could not be cancelled "
                "because of a data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise

    return await _load_trade_document(
        db,
        company_id=company_id,
        document_id=document_id_value,
    )
