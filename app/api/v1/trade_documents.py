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
from app.schemas.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocationCreateRequest,
    InvoiceFulfillmentAllocationResponse,
    InvoiceFulfillmentReconciliationAllocationResponse,
    InvoiceFulfillmentReconciliationLineResponse,
    InvoiceFulfillmentReconciliationResponse,
)

from app.schemas.trade_document import (
    SalesOrderFulfillmentRequest,
    SalesOrderFulfillmentResponse,
    SalesOrderFulfillmentReversalRequest,
    SalesOrderFulfillmentReversalResponse,
    TradeDocumentCreate,
    TradeDocumentResponse,
    TradeDocumentUpdate,
)
from app.services.invoice_fulfillment_allocation_service import (
    DuplicateActiveInvoiceFulfillmentAllocationError,
    FulfillmentOverAllocationError,
    InvoiceFulfillmentAllocationContractError,
    InvoiceFulfillmentAllocationCounterpartyError,
    InvoiceFulfillmentAllocationCurrencyError,
    InvoiceFulfillmentAllocationDirectionError,
    InvoiceFulfillmentAllocationError,
    InvoiceFulfillmentAllocationNotFoundError,
    InvoiceFulfillmentAllocationProductError,
    InvoiceFulfillmentAllocationQuantityError,
    InvoiceFulfillmentAllocationReversalStateError,
    InvoiceFulfillmentAllocationStatusError,
    InvoiceFulfillmentAllocationTypeError,
    InvoiceFulfillmentAllocationWarehouseError,
    InvoiceOverAllocationError,
    create_invoice_fulfillment_allocation,
    get_invoice_fulfillment_allocation_history,
    get_invoice_fulfillment_reconciliation,
    reverse_invoice_fulfillment_allocation,
)

from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)
from app.services.trade_document_validation import (
    TradeDocumentValidationError,
    validate_trade_document_contract,
)


from app.services.document_posting import (
    DocumentPostingError,
)
from app.services.reservation_persistence_service import (
    ReservationPersistenceError,
)
from app.services.trade_fulfillment_service import (
    PurchaseOrderFulfillmentError,
    PurchaseOrderFulfillmentRequestLine,
    PurchaseOrderFulfillmentReversalNotFoundError,
    SalesOrderFulfillmentError,
    SalesOrderFulfillmentRequestLine,
    SalesOrderFulfillmentReversalNotFoundError,
    execute_purchase_order_fulfillment,
    execute_purchase_order_fulfillment_reversal,
    execute_sales_order_fulfillment,
    execute_sales_order_fulfillment_reversal,
)
from app.services.trade_document_lifecycle_service import (
    PurchaseOrderLinesRequiredError,
    PurchaseOrderNotFoundError,
    PurchaseOrderReferenceError,
    PurchaseOrderStatusError,
    PurchaseOrderTypeError,
    PurchaseOrderWarehouseRequiredError,
    SalesOrderLinesRequiredError,
    SalesOrderNotFoundError,
    SalesOrderReferenceError,
    SalesOrderReservationStateError,
    SalesOrderStatusError,
    SalesOrderTypeError,
    SalesOrderWarehouseRequiredError,
    TradeInvoiceAmountError,
    TradeInvoiceLinesRequiredError,
    TradeInvoiceNotFoundError,
    TradeInvoiceOpenItemStateError,
    TradeInvoiceReferenceError,
    TradeInvoiceStatusError,
    TradeInvoiceTypeError,
    cancel_purchase_invoice,
    cancel_purchase_order,
    cancel_sales_invoice,
    cancel_sales_order,
    confirm_purchase_invoice,
    confirm_purchase_order,
    confirm_sales_invoice,
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
                status.HTTP_422_UNPROCESSABLE_CONTENT
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



async def _get_trade_document_lifecycle_identity(
    db: AsyncSession,
    *,
    company_id: int,
    document_id: int,
) -> tuple[
    TradeDirection,
    TradeDocumentKind,
]:
    """
    Resolve direction + kind for lifecycle API dispatch.

    Domain services still acquire row locks and revalidate the
    document before mutating state. This query is dispatch metadata.
    """
    row = (
        await db.execute(
            select(
                TradeDocument.direction,
                TradeDocument.kind,
            ).where(
                TradeDocument.id == document_id,
                TradeDocument.company_id == company_id,
            )
        )
    ).one_or_none()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trade document not found",
        )

    return (
        row[0],
        row[1],
    )


async def _get_trade_document_direction(
    db: AsyncSession,
    *,
    company_id: int,
    document_id: int,
) -> TradeDirection:
    """
    Compatibility helper retained for existing internal imports/tests.
    """
    direction, _kind = (
        await _get_trade_document_lifecycle_identity(
            db,
            company_id=company_id,
            document_id=document_id,
        )
    )

    return direction



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
                    tax_rate_code=(
                        line_data.tax_rate_code
                    ),
                    tax_recognition_method=(
                        line_data.tax_recognition_method
                    ),
                    tax_price_mode=(
                        line_data.tax_price_mode
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
                    status.HTTP_422_UNPROCESSABLE_CONTENT
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
                        status.HTTP_422_UNPROCESSABLE_CONTENT
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
                        tax_rate_code=(
                            line_data.tax_rate_code
                        ),
                        tax_recognition_method=(
                            line_data
                            .tax_recognition_method
                        ),
                        tax_price_mode=(
                            line_data.tax_price_mode
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
    Confirm a draft Trade Order or Trade Invoice atomically.

    Compatibility function name is retained.
    """
    try:
        (
            direction,
            kind,
        ) = await _get_trade_document_lifecycle_identity(
            db,
            company_id=company_id,
            document_id=document_id,
        )

        if kind == TradeDocumentKind.ORDER:
            if direction == TradeDirection.SALE:
                document = await confirm_sales_order(
                    db,
                    company_id=company_id,
                    document_id=document_id,
                )

            elif direction == TradeDirection.PURCHASE:
                document = await confirm_purchase_order(
                    db,
                    company_id=company_id,
                    document_id=document_id,
                )

            else:
                raise HTTPException(
                    status_code=(
                        status.HTTP_422_UNPROCESSABLE_CONTENT
                    ),
                    detail="Unsupported trade document direction",
                )

        elif kind == TradeDocumentKind.INVOICE:
            if direction == TradeDirection.SALE:
                document = await confirm_sales_invoice(
                    db,
                    company_id=company_id,
                    document_id=document_id,
                )

            elif direction == TradeDirection.PURCHASE:
                document = await confirm_purchase_invoice(
                    db,
                    company_id=company_id,
                    document_id=document_id,
                )

            else:
                raise HTTPException(
                    status_code=(
                        status.HTTP_422_UNPROCESSABLE_CONTENT
                    ),
                    detail="Unsupported trade document direction",
                )

        else:
            raise HTTPException(
                status_code=(
                    status.HTTP_422_UNPROCESSABLE_CONTENT
                ),
                detail="Unsupported trade document kind",
            )

        document_id_value = document.id

        await db.commit()

    except HTTPException:
        await db.rollback()
        raise

    except (
        SalesOrderNotFoundError,
        PurchaseOrderNotFoundError,
        TradeInvoiceNotFoundError,
    ) as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except (
        SalesOrderStatusError,
        PurchaseOrderStatusError,
        TradeInvoiceStatusError,
        TradeInvoiceOpenItemStateError,
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
        SalesOrderReferenceError,
        PurchaseOrderTypeError,
        PurchaseOrderLinesRequiredError,
        PurchaseOrderWarehouseRequiredError,
        PurchaseOrderReferenceError,
        TradeInvoiceTypeError,
        TradeInvoiceLinesRequiredError,
        TradeInvoiceAmountError,
        TradeInvoiceReferenceError,
    ) as exc:
        await db.rollback()

        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
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
                "Trade document could not be confirmed "
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
    Cancel a Trade Order or Trade Invoice atomically.

    Compatibility function name is retained.
    """
    try:
        (
            direction,
            kind,
        ) = await _get_trade_document_lifecycle_identity(
            db,
            company_id=company_id,
            document_id=document_id,
        )

        if kind == TradeDocumentKind.ORDER:
            if direction == TradeDirection.SALE:
                document = await cancel_sales_order(
                    db,
                    company_id=company_id,
                    document_id=document_id,
                )

            elif direction == TradeDirection.PURCHASE:
                document = await cancel_purchase_order(
                    db,
                    company_id=company_id,
                    document_id=document_id,
                )

            else:
                raise HTTPException(
                    status_code=(
                        status.HTTP_422_UNPROCESSABLE_CONTENT
                    ),
                    detail="Unsupported trade document direction",
                )

        elif kind == TradeDocumentKind.INVOICE:
            if direction == TradeDirection.SALE:
                document = await cancel_sales_invoice(
                    db,
                    company_id=company_id,
                    document_id=document_id,
                )

            elif direction == TradeDirection.PURCHASE:
                document = await cancel_purchase_invoice(
                    db,
                    company_id=company_id,
                    document_id=document_id,
                )

            else:
                raise HTTPException(
                    status_code=(
                        status.HTTP_422_UNPROCESSABLE_CONTENT
                    ),
                    detail="Unsupported trade document direction",
                )

        else:
            raise HTTPException(
                status_code=(
                    status.HTTP_422_UNPROCESSABLE_CONTENT
                ),
                detail="Unsupported trade document kind",
            )

        document_id_value = document.id

        await db.commit()

    except HTTPException:
        await db.rollback()
        raise

    except (
        SalesOrderNotFoundError,
        PurchaseOrderNotFoundError,
        TradeInvoiceNotFoundError,
    ) as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except (
        SalesOrderStatusError,
        SalesOrderReservationStateError,
        PurchaseOrderStatusError,
        TradeInvoiceStatusError,
        TradeInvoiceOpenItemStateError,
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
        PurchaseOrderTypeError,
        PurchaseOrderLinesRequiredError,
        PurchaseOrderWarehouseRequiredError,
        TradeInvoiceTypeError,
        TradeInvoiceLinesRequiredError,
        TradeInvoiceAmountError,
        TradeInvoiceReferenceError,
    ) as exc:
        await db.rollback()

        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail=str(exc),
        ) from exc

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Trade document could not be cancelled "
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
    "/{document_id}/fulfill",
    response_model=SalesOrderFulfillmentResponse,
)
async def fulfill_trade_document_sales_order(
    company_id: int,
    document_id: int,
    data: SalesOrderFulfillmentRequest,
    current_user: User = Depends(
        get_current_user
    ),
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "trade_documents.fulfill"
        )
    ),
):
    """
    Fulfill part or all of a confirmed Trade ORDER.

    SALE ORDER     -> warehouse ISSUE.
    PURCHASE ORDER -> warehouse RECEIPT.

    Trade INVOICE is not a warehouse fulfillment source.
    """
    try:
        (
            direction,
            kind,
        ) = await _get_trade_document_lifecycle_identity(
            db,
            company_id=company_id,
            document_id=document_id,
        )

        if kind != TradeDocumentKind.ORDER:
            raise HTTPException(
                status_code=(
                    status.HTTP_422_UNPROCESSABLE_CONTENT
                ),
                detail=(
                    "Only trade document kind 'order' "
                    "can be fulfilled"
                ),
            )

        if direction == TradeDirection.SALE:
            execution_result = (
                await execute_sales_order_fulfillment(
                    db,
                    company_id=company_id,
                    trade_document_id=document_id,
                    warehouse_document_number=(
                        data.warehouse_document_number
                    ),
                    document_date=data.document_date,
                    accounting_rule_id=(
                        data.accounting_rule_id
                    ),
                    created_by=current_user.id,
                    request_lines=[
                        SalesOrderFulfillmentRequestLine(
                            trade_document_line_id=(
                                line.trade_document_line_id
                            ),
                            quantity=line.quantity,
                        )
                        for line in data.lines
                    ],
                )
            )

            trade_document_id_value = (
                execution_result.sales_order.id
            )

        elif direction == TradeDirection.PURCHASE:
            execution_result = (
                await execute_purchase_order_fulfillment(
                    db,
                    company_id=company_id,
                    trade_document_id=document_id,
                    warehouse_document_number=(
                        data.warehouse_document_number
                    ),
                    document_date=data.document_date,
                    accounting_rule_id=(
                        data.accounting_rule_id
                    ),
                    created_by=current_user.id,
                    request_lines=[
                        PurchaseOrderFulfillmentRequestLine(
                            trade_document_line_id=(
                                line.trade_document_line_id
                            ),
                            quantity=line.quantity,
                        )
                        for line in data.lines
                    ],
                )
            )

            trade_document_id_value = (
                execution_result.purchase_order.id
            )

        else:
            raise HTTPException(
                status_code=(
                    status.HTTP_422_UNPROCESSABLE_CONTENT
                ),
                detail="Unsupported trade document direction",
            )

        warehouse_document_id = (
            execution_result.warehouse_document.id
        )

        fulfillment_id = (
            execution_result.fulfillment.id
        )

        journal_entry_id = (
            execution_result.journal_entry.id
        )

        await db.commit()

    except HTTPException:
        await db.rollback()
        raise

    except (
        SalesOrderNotFoundError,
        PurchaseOrderNotFoundError,
    ) as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except (
        SalesOrderFulfillmentError,
        PurchaseOrderFulfillmentError,
        ReservationPersistenceError,
        DocumentPostingError,
    ) as exc:
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
                "Trade order fulfillment could not be "
                "completed because of a data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise

    trade_document = await _load_trade_document(
        db,
        company_id=company_id,
        document_id=trade_document_id_value,
    )

    return SalesOrderFulfillmentResponse(
        trade_document=trade_document,
        warehouse_document_id=warehouse_document_id,
        fulfillment_id=fulfillment_id,
        journal_entry_id=journal_entry_id,
    )


@router.post(
    "/{document_id}/fulfillments/{fulfillment_id}/reverse",
    response_model=(
        SalesOrderFulfillmentReversalResponse
    ),
)
async def reverse_trade_document_sales_order_fulfillment(
    company_id: int,
    document_id: int,
    fulfillment_id: int,
    data: SalesOrderFulfillmentReversalRequest,
    current_user: User = Depends(
        get_current_user
    ),
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "trade_documents.fulfillments.reverse"
        )
    ),
):
    """
    Reverse one exact persistent Trade ORDER fulfillment.

    Trade INVOICE has no warehouse fulfillment to reverse.
    """
    try:
        (
            direction,
            kind,
        ) = await _get_trade_document_lifecycle_identity(
            db,
            company_id=company_id,
            document_id=document_id,
        )

        if kind != TradeDocumentKind.ORDER:
            raise HTTPException(
                status_code=(
                    status.HTTP_422_UNPROCESSABLE_CONTENT
                ),
                detail=(
                    "Only trade document kind 'order' "
                    "can have fulfillment reversal"
                ),
            )

        if direction == TradeDirection.SALE:
            result = (
                await execute_sales_order_fulfillment_reversal(
                    db,
                    company_id=company_id,
                    trade_document_id=document_id,
                    fulfillment_id=fulfillment_id,
                    reversal_date=data.reversal_date,
                    reversed_by=current_user.id,
                )
            )

            document_id_value = (
                result.sales_order.id
            )

        elif direction == TradeDirection.PURCHASE:
            result = (
                await execute_purchase_order_fulfillment_reversal(
                    db,
                    company_id=company_id,
                    trade_document_id=document_id,
                    fulfillment_id=fulfillment_id,
                    reversal_date=data.reversal_date,
                    reversed_by=current_user.id,
                )
            )

            document_id_value = (
                result.purchase_order.id
            )

        else:
            raise HTTPException(
                status_code=(
                    status.HTTP_422_UNPROCESSABLE_CONTENT
                ),
                detail="Unsupported trade document direction",
            )

        warehouse_document_id = (
            result.warehouse_document.id
        )

        fulfillment_id_value = (
            result.fulfillment.id
        )

        await db.commit()

    except HTTPException:
        await db.rollback()
        raise

    except (
        SalesOrderNotFoundError,
        PurchaseOrderNotFoundError,
        SalesOrderFulfillmentReversalNotFoundError,
        PurchaseOrderFulfillmentReversalNotFoundError,
    ) as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except ValueError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail=str(exc),
        ) from exc

    except (
        SalesOrderFulfillmentError,
        PurchaseOrderFulfillmentError,
        ReservationPersistenceError,
    ) as exc:
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
                "Trade order fulfillment could not "
                "be reversed because of a data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise

    trade_document = await _load_trade_document(
        db,
        company_id=company_id,
        document_id=document_id_value,
    )

    return SalesOrderFulfillmentReversalResponse(
        trade_document=trade_document,
        warehouse_document_id=warehouse_document_id,
        fulfillment_id=fulfillment_id_value,
    )


# ============================================================
# STEP 15.5 - INVOICE FULFILLMENT ALLOCATION API
# ============================================================


def _invoice_fulfillment_http_exception(
    exc: InvoiceFulfillmentAllocationError,
) -> HTTPException:
    if isinstance(
        exc,
        InvoiceFulfillmentAllocationNotFoundError,
    ):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    if isinstance(
        exc,
        (
            InvoiceFulfillmentAllocationTypeError,
            InvoiceFulfillmentAllocationDirectionError,
            InvoiceFulfillmentAllocationCounterpartyError,
            InvoiceFulfillmentAllocationContractError,
            InvoiceFulfillmentAllocationCurrencyError,
            InvoiceFulfillmentAllocationProductError,
            InvoiceFulfillmentAllocationWarehouseError,
            InvoiceFulfillmentAllocationQuantityError,
        ),
    ):
        return HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail=str(exc),
        )

    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=str(exc),
    )


@router.post(
    "/{invoice_id}/fulfillment-allocations",
    response_model=(
        InvoiceFulfillmentAllocationResponse
    ),
)
async def create_trade_invoice_fulfillment_allocation(
    company_id: int,
    invoice_id: int,
    data: InvoiceFulfillmentAllocationCreateRequest,
    current_user: User = Depends(
        get_current_user
    ),
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "trade_documents.allocations.manage"
        )
    ),
):
    """
    Create one ACTIVE quantity match between a Trade Invoice
    line and one persistent Trade Fulfillment line.
    """

    try:
        allocation = (
            await create_invoice_fulfillment_allocation(
                db,
                company_id=company_id,
                invoice_id=invoice_id,
                invoice_line_id=data.invoice_line_id,
                fulfillment_id=data.fulfillment_id,
                fulfillment_line_id=(
                    data.fulfillment_line_id
                ),
                quantity=data.quantity,
                created_by=current_user.id,
            )
        )

        await db.commit()

        return allocation

    except InvoiceFulfillmentAllocationError as exc:
        await db.rollback()

        raise _invoice_fulfillment_http_exception(
            exc
        ) from exc

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Invoice Fulfillment allocation could not "
                "be created because of a data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise


@router.get(
    "/{invoice_id}/fulfillment-allocations",
    response_model=list[
        InvoiceFulfillmentAllocationResponse
    ],
)
async def list_trade_invoice_fulfillment_allocations(
    company_id: int,
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "trade_documents.allocations.read"
        )
    ),
):
    """
    Return complete allocation history for one Trade Invoice.

    ACTIVE and REVERSED records are both retained.
    """

    try:
        (
            _invoice,
            allocations,
        ) = (
            await get_invoice_fulfillment_allocation_history(
                db,
                company_id=company_id,
                invoice_id=invoice_id,
            )
        )

        return list(
            allocations
        )

    except InvoiceFulfillmentAllocationError as exc:
        raise _invoice_fulfillment_http_exception(
            exc
        ) from exc


@router.post(
    "/{invoice_id}/fulfillment-allocations/"
    "{allocation_id}/reverse",
    response_model=(
        InvoiceFulfillmentAllocationResponse
    ),
)
async def reverse_trade_invoice_fulfillment_allocation(
    company_id: int,
    invoice_id: int,
    allocation_id: int,
    current_user: User = Depends(
        get_current_user
    ),
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "trade_documents.allocations.manage"
        )
    ),
):
    """
    Reverse one ACTIVE Invoice/Fulfillment allocation.

    The persistent row remains as historical REVERSED audit
    evidence.
    """

    try:
        allocation = (
            await reverse_invoice_fulfillment_allocation(
                db,
                company_id=company_id,
                invoice_id=invoice_id,
                allocation_id=allocation_id,
                reversed_by=current_user.id,
            )
        )

        await db.commit()

        return allocation

    except InvoiceFulfillmentAllocationError as exc:
        await db.rollback()

        raise _invoice_fulfillment_http_exception(
            exc
        ) from exc

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Invoice Fulfillment allocation could not "
                "be reversed because of a data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise


@router.get(
    "/{invoice_id}/reconciliation",
    response_model=(
        InvoiceFulfillmentReconciliationResponse
    ),
)
async def get_trade_invoice_fulfillment_reconciliation(
    company_id: int,
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "trade_documents.allocations.read"
        )
    ),
):
    """
    Return quantity-based commercial recognition
    reconciliation for one Trade Invoice.
    """

    try:
        reconciliation = (
            await get_invoice_fulfillment_reconciliation(
                db,
                company_id=company_id,
                invoice_id=invoice_id,
            )
        )

    except InvoiceFulfillmentAllocationError as exc:
        raise _invoice_fulfillment_http_exception(
            exc
        ) from exc

    invoice = reconciliation.invoice

    lines = []

    for line in reconciliation.lines:
        allocation_responses = []

        for item in line.allocations:
            allocation = item.allocation

            allocation_responses.append(
                InvoiceFulfillmentReconciliationAllocationResponse(
                    id=allocation.id,
                    invoice_line_id=(
                        allocation.invoice_line_id
                    ),
                    fulfillment_id=(
                        allocation.fulfillment_id
                    ),
                    fulfillment_line_id=(
                        allocation.fulfillment_line_id
                    ),
                    order_id=allocation.order_id,
                    order_line_id=(
                        allocation.order_line_id
                    ),
                    product_id=allocation.product_id,
                    quantity=allocation.quantity,
                    status=allocation.status,
                    fulfillment_line_quantity=(
                        item.fulfillment_line_quantity
                    ),
                    fulfillment_line_active_allocated_quantity=(
                        item
                        .fulfillment_line_active_allocated_quantity
                    ),
                    fulfillment_line_remaining_quantity=(
                        item
                        .fulfillment_line_remaining_quantity
                    ),
                    created_by=allocation.created_by,
                    created_at=allocation.created_at,
                    reversed_by=allocation.reversed_by,
                    reversed_at=allocation.reversed_at,
                )
            )

        invoice_line = line.invoice_line

        lines.append(
            InvoiceFulfillmentReconciliationLineResponse(
                invoice_line_id=invoice_line.id,
                line_number=invoice_line.line_number,
                product_id=invoice_line.product_id,
                warehouse_id=invoice_line.warehouse_id,
                invoice_quantity=invoice_line.quantity,
                active_allocated_quantity=(
                    line.active_allocated_quantity
                ),
                remaining_quantity=(
                    line.remaining_quantity
                ),
                fully_allocated=line.fully_allocated,
                allocations=allocation_responses,
            )
        )

    return InvoiceFulfillmentReconciliationResponse(
        company_id=invoice.company_id,
        invoice_id=invoice.id,
        direction=invoice.direction,
        status=invoice.status,
        counterparty_id=invoice.counterparty_id,
        contract_id=invoice.contract_id,
        currency_code=invoice.currency_code,
        fully_allocated=(
            reconciliation.fully_allocated
        ),
        lines=lines,
    )
