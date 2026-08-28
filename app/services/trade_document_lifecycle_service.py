from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.company import Company
from app.models.contract import Contract
from app.models.counterparty import Counterparty
from app.models.product import Product
from app.models.trade_document import TradeDocument
from app.models.trade_document_line import TradeDocumentLine
from app.models.warehouse import Warehouse
from app.services.reservation_persistence_service import (
    get_reserved_quantity_for_source_line,
    release_source_line,
    reserve_source_line,
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


class TradeDocumentLifecycleError(Exception):
    """Base trade-document lifecycle error."""


class SalesOrderNotFoundError(
    TradeDocumentLifecycleError
):
    """Sales order was not found in the company."""


class SalesOrderTypeError(
    TradeDocumentLifecycleError
):
    """Document is not a sales order."""


class SalesOrderStatusError(
    TradeDocumentLifecycleError
):
    """Sales order is in an invalid lifecycle state."""


class SalesOrderLinesRequiredError(
    TradeDocumentLifecycleError
):
    """Sales order must contain at least one line."""


class SalesOrderWarehouseRequiredError(
    TradeDocumentLifecycleError
):
    """Every sales-order line must have a warehouse."""


class SalesOrderReferenceError(
    TradeDocumentLifecycleError
):
    """Base error for invalid confirmation references."""


class SalesOrderCompanyInvalidError(
    SalesOrderReferenceError
):
    """Company no longer exists or is inactive."""


class SalesOrderCounterpartyInvalidError(
    SalesOrderReferenceError
):
    """Counterparty no longer exists or is inactive."""


class SalesOrderContractInvalidError(
    SalesOrderReferenceError
):
    """Contract is missing or no longer valid."""


class SalesOrderProductInvalidError(
    SalesOrderReferenceError
):
    """One or more products are missing or inactive."""


class SalesOrderWarehouseInvalidError(
    SalesOrderReferenceError
):
    """One or more warehouses are missing or inactive."""


class PurchaseOrderNotFoundError(
    TradeDocumentLifecycleError
):
    """Purchase order was not found in the company."""


class PurchaseOrderTypeError(
    TradeDocumentLifecycleError
):
    """Document is not a purchase order."""


class PurchaseOrderStatusError(
    TradeDocumentLifecycleError
):
    """Purchase order is in an invalid lifecycle state."""


class PurchaseOrderLinesRequiredError(
    TradeDocumentLifecycleError
):
    """Purchase order must contain at least one line."""


class PurchaseOrderWarehouseRequiredError(
    TradeDocumentLifecycleError
):
    """Every purchase-order line must have a warehouse."""


class PurchaseOrderReferenceError(
    TradeDocumentLifecycleError
):
    """Base error for invalid purchase-order references."""


class PurchaseOrderCompanyInvalidError(
    PurchaseOrderReferenceError
):
    """Company no longer exists or is inactive."""


class PurchaseOrderCounterpartyInvalidError(
    PurchaseOrderReferenceError
):
    """Counterparty no longer exists or is inactive."""


class PurchaseOrderContractInvalidError(
    PurchaseOrderReferenceError
):
    """Contract is missing or no longer valid."""


class PurchaseOrderProductInvalidError(
    PurchaseOrderReferenceError
):
    """One or more products are missing or inactive."""


class PurchaseOrderWarehouseInvalidError(
    PurchaseOrderReferenceError
):
    """One or more warehouses are missing or inactive."""


def validate_sales_order_confirmation(
    document: TradeDocument,
) -> None:
    """
    Validate structural lifecycle requirements.

    Reference validity is checked separately immediately before
    reservation movements are created.
    """

    if document.direction != TradeDirection.SALE:
        raise SalesOrderTypeError(
            "Only sale trade documents can be "
            "confirmed as sales orders"
        )

    if document.kind != TradeDocumentKind.ORDER:
        raise SalesOrderTypeError(
            "Only trade document kind 'order' can be "
            "confirmed as a sales order"
        )

    if document.status != TradeDocumentStatus.DRAFT:
        raise SalesOrderStatusError(
            "Only draft sales orders can be confirmed"
        )

    if not document.lines:
        raise SalesOrderLinesRequiredError(
            "Sales order must contain at least one line"
        )

    missing_warehouse_lines = [
        line.line_number
        for line in document.lines
        if line.warehouse_id is None
    ]

    if missing_warehouse_lines:
        raise SalesOrderWarehouseRequiredError(
            "Warehouse is required before confirmation "
            "for sales order lines: "
            + ", ".join(
                str(line_number)
                for line_number
                in sorted(
                    missing_warehouse_lines
                )
            )
        )


async def _revalidate_order_references(
    db: AsyncSession,
    *,
    document: TradeDocument,
    company_error: type[TradeDocumentLifecycleError],
    counterparty_error: type[TradeDocumentLifecycleError],
    contract_error: type[TradeDocumentLifecycleError],
    product_error: type[TradeDocumentLifecycleError],
    warehouse_error: type[TradeDocumentLifecycleError],
) -> None:
    """
    Revalidate mutable order references immediately before
    confirmation.

    Both Sales Orders and Purchase Orders use the same
    company/counterparty/contract/product/warehouse integrity
    rules. Direction-specific contract validation remains
    delegated to validate_trade_document_contract().
    """
    company = (
        await db.execute(
            select(
                Company
            ).where(
                Company.id
                == document.company_id,
                Company.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()

    if company is None:
        raise company_error(
            "Company is inactive or does not exist"
        )

    counterparty = (
        await db.execute(
            select(
                Counterparty
            ).where(
                Counterparty.id
                == document.counterparty_id,
                Counterparty.company_id
                == document.company_id,
                Counterparty.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()

    if counterparty is None:
        raise counterparty_error(
            "Counterparty is inactive or does not exist"
        )

    if document.contract_id is not None:
        contract = (
            await db.execute(
                select(
                    Contract
                ).where(
                    Contract.id
                    == document.contract_id,
                    Contract.company_id
                    == document.company_id,
                )
            )
        ).scalar_one_or_none()

        if contract is None:
            raise contract_error(
                "Contract does not exist"
            )

        try:
            validate_trade_document_contract(
                contract=contract,
                company_id=document.company_id,
                counterparty_id=(
                    document.counterparty_id
                ),
                direction=document.direction,
                document_date=(
                    document.document_date
                ),
                currency_code=(
                    document.currency_code
                ),
            )
        except TradeDocumentValidationError as exc:
            raise contract_error(
                str(exc)
            ) from exc

    product_ids = {
        line.product_id
        for line in document.lines
    }

    valid_product_ids = set(
        (
            await db.execute(
                select(
                    Product.id
                ).where(
                    Product.company_id
                    == document.company_id,
                    Product.id.in_(
                        product_ids
                    ),
                    Product.is_active.is_(True),
                )
            )
        ).scalars().all()
    )

    invalid_product_ids = (
        product_ids
        - valid_product_ids
    )

    if invalid_product_ids:
        raise product_error(
            "Inactive, missing, or foreign products: "
            + ", ".join(
                str(product_id)
                for product_id
                in sorted(
                    invalid_product_ids
                )
            )
        )

    warehouse_ids = {
        line.warehouse_id
        for line in document.lines
        if line.warehouse_id is not None
    }

    valid_warehouse_ids = set(
        (
            await db.execute(
                select(
                    Warehouse.id
                ).where(
                    Warehouse.company_id
                    == document.company_id,
                    Warehouse.id.in_(
                        warehouse_ids
                    ),
                    Warehouse.is_active.is_(True),
                )
            )
        ).scalars().all()
    )

    invalid_warehouse_ids = (
        warehouse_ids
        - valid_warehouse_ids
    )

    if invalid_warehouse_ids:
        raise warehouse_error(
            "Inactive, missing, or foreign warehouses: "
            + ", ".join(
                str(warehouse_id)
                for warehouse_id
                in sorted(
                    invalid_warehouse_ids
                )
            )
        )


async def revalidate_sales_order_references(
    db: AsyncSession,
    *,
    document: TradeDocument,
) -> None:
    await _revalidate_order_references(
        db,
        document=document,
        company_error=(
            SalesOrderCompanyInvalidError
        ),
        counterparty_error=(
            SalesOrderCounterpartyInvalidError
        ),
        contract_error=(
            SalesOrderContractInvalidError
        ),
        product_error=(
            SalesOrderProductInvalidError
        ),
        warehouse_error=(
            SalesOrderWarehouseInvalidError
        ),
    )


async def revalidate_purchase_order_references(
    db: AsyncSession,
    *,
    document: TradeDocument,
) -> None:
    await _revalidate_order_references(
        db,
        document=document,
        company_error=(
            PurchaseOrderCompanyInvalidError
        ),
        counterparty_error=(
            PurchaseOrderCounterpartyInvalidError
        ),
        contract_error=(
            PurchaseOrderContractInvalidError
        ),
        product_error=(
            PurchaseOrderProductInvalidError
        ),
        warehouse_error=(
            PurchaseOrderWarehouseInvalidError
        ),
    )


def reservation_lock_order(
    document: TradeDocument,
) -> tuple[TradeDocumentLine, ...]:
    """
    Return lines in deterministic stock-lock order.

    Multiple sales orders may reserve overlapping stock keys.
    Sorting by product/warehouse before acquiring StockBalance
    locks matches warehouse posting and reduces deadlock risk.
    """

    validate_sales_order_confirmation(
        document
    )

    return tuple(
        sorted(
            document.lines,
            key=lambda line: (
                line.product_id,
                line.warehouse_id,
                line.id or 0,
            ),
        )
    )


async def get_locked_trade_document(
    db: AsyncSession,
    *,
    company_id: int,
    document_id: int,
) -> TradeDocument:
    """
    Lock the TradeDocument header.

    Draft PATCH locks the same header, so confirmation cannot
    race with draft editing.
    """

    result = await db.execute(
        select(
            TradeDocument
        )
        .options(
            selectinload(
                TradeDocument.lines
            )
        )
        .where(
            TradeDocument.id
            == document_id,
            TradeDocument.company_id
            == company_id,
        )
        .with_for_update()
    )

    document = (
        result.scalar_one_or_none()
    )

    if document is None:
        raise SalesOrderNotFoundError(
            "Trade document not found"
        )

    return document


async def confirm_sales_order(
    db: AsyncSession,
    *,
    company_id: int,
    document_id: int,
) -> TradeDocument:
    """
    Confirm one Sales Order atomically.

    Caller owns COMMIT / ROLLBACK.

    Sequence:
        1. lock TradeDocument header
        2. validate sale/order/draft structure
        3. revalidate mutable business references
        4. reserve full quantity of every line
        5. set CONFIRMED + confirmed_at
        6. flush

    Any failure leaves the transaction for the caller to roll back.
    """

    document = await get_locked_trade_document(
        db,
        company_id=company_id,
        document_id=document_id,
    )

    validate_sales_order_confirmation(
        document
    )

    await revalidate_sales_order_references(
        db,
        document=document,
    )

    lines = reservation_lock_order(
        document
    )

    for line in lines:
        await reserve_source_line(
            db,
            company_id=company_id,
            source_document_id=document.id,
            source_document_line_id=line.id,
            quantity=line.quantity,
        )

    document.status = (
        TradeDocumentStatus.CONFIRMED
    )

    document.confirmed_at = datetime.now(
        timezone.utc
    )

    await db.flush()

    return document


def validate_purchase_order_confirmation(
    document: TradeDocument,
) -> None:
    """
    Validate Purchase Order confirmation structure.

    Purchase confirmation creates no stock reservation.
    Warehouse is still mandatory because future receipt
    fulfillment derives its target warehouse exclusively from
    the persistent TradeDocumentLine.
    """
    if document.direction != TradeDirection.PURCHASE:
        raise PurchaseOrderTypeError(
            "Only purchase trade documents can be "
            "confirmed as purchase orders"
        )

    if document.kind != TradeDocumentKind.ORDER:
        raise PurchaseOrderTypeError(
            "Only trade document kind 'order' can be "
            "confirmed as a purchase order"
        )

    if document.status != TradeDocumentStatus.DRAFT:
        raise PurchaseOrderStatusError(
            "Only draft purchase orders can be confirmed"
        )

    if not document.lines:
        raise PurchaseOrderLinesRequiredError(
            "Purchase order must contain at least one line"
        )

    missing_warehouse_lines = [
        line.line_number
        for line in document.lines
        if line.warehouse_id is None
    ]

    if missing_warehouse_lines:
        raise PurchaseOrderWarehouseRequiredError(
            "Warehouse is required before confirmation "
            "for purchase order lines: "
            + ", ".join(
                str(line_number)
                for line_number
                in sorted(
                    missing_warehouse_lines
                )
            )
        )


async def get_locked_purchase_order(
    db: AsyncSession,
    *,
    company_id: int,
    document_id: int,
) -> TradeDocument:
    """
    Lock one TradeDocument for Purchase Order lifecycle
    operations.

    The header lock prevents confirmation from racing with
    draft PATCH operations.
    """
    result = await db.execute(
        select(
            TradeDocument
        )
        .options(
            selectinload(
                TradeDocument.lines
            )
        )
        .where(
            TradeDocument.id
            == document_id,
            TradeDocument.company_id
            == company_id,
        )
        .with_for_update()
    )

    document = (
        result.scalar_one_or_none()
    )

    if document is None:
        raise PurchaseOrderNotFoundError(
            "Trade document not found"
        )

    return document


async def confirm_purchase_order(
    db: AsyncSession,
    *,
    company_id: int,
    document_id: int,
) -> TradeDocument:
    """
    Confirm one Purchase Order atomically.

    Caller owns COMMIT / ROLLBACK.

    Sequence:

        1. lock TradeDocument header
        2. validate purchase/order/draft structure
        3. revalidate mutable business references
        4. set CONFIRMED + confirmed_at
        5. flush

    Unlike Sales Order confirmation, Purchase Order
    confirmation deliberately creates no ReservationMovement.
    Incoming stock does not exist yet and therefore cannot be
    reserved as warehouse inventory.
    """
    document = await get_locked_purchase_order(
        db,
        company_id=company_id,
        document_id=document_id,
    )

    validate_purchase_order_confirmation(
        document
    )

    await revalidate_purchase_order_references(
        db,
        document=document,
    )

    document.status = (
        TradeDocumentStatus.CONFIRMED
    )

    document.confirmed_at = datetime.now(
        timezone.utc
    )

    await db.flush()

    return document


def validate_purchase_order_cancellation(
    document: TradeDocument,
) -> None:
    """
    Validate whether a Purchase Order may be cancelled.

    Allowed:
        DRAFT
        CONFIRMED

    Rejected:
        PARTIALLY_FULFILLED
        FULFILLED
        CANCELLED

    Purchase Orders have no stock reservations, so cancellation
    requires no ReservationMovement release.
    """
    if document.direction != TradeDirection.PURCHASE:
        raise PurchaseOrderTypeError(
            "Only purchase trade documents can be "
            "cancelled as purchase orders"
        )

    if document.kind != TradeDocumentKind.ORDER:
        raise PurchaseOrderTypeError(
            "Only trade document kind 'order' can be "
            "cancelled as a purchase order"
        )

    if document.status not in (
        TradeDocumentStatus.DRAFT,
        TradeDocumentStatus.CONFIRMED,
    ):
        raise PurchaseOrderStatusError(
            "Only draft or confirmed purchase orders "
            "can be cancelled"
        )


async def cancel_purchase_order(
    db: AsyncSession,
    *,
    company_id: int,
    document_id: int,
) -> TradeDocument:
    """
    Cancel one Purchase Order atomically.

    Caller owns COMMIT / ROLLBACK.

    DRAFT:
        set CANCELLED

    CONFIRMED:
        set CANCELLED

    No reservation movements are created or released.

    PARTIALLY_FULFILLED and FULFILLED are rejected because
    already received inventory must first be corrected through
    Purchase Order fulfillment reversal / return semantics.
    """
    document = await get_locked_purchase_order(
        db,
        company_id=company_id,
        document_id=document_id,
    )

    validate_purchase_order_cancellation(
        document
    )

    document.status = (
        TradeDocumentStatus.CANCELLED
    )

    document.cancelled_at = datetime.now(
        timezone.utc
    )

    await db.flush()

    return document


class SalesOrderReservationStateError(
    TradeDocumentLifecycleError
):
    """Sales-order reservation state is internally invalid."""


def validate_sales_order_cancellation(
    document: TradeDocument,
) -> None:
    """
    Validate whether a Sales Order may be cancelled.

    Allowed:
        DRAFT
        CONFIRMED

    Not allowed:
        PARTIALLY_FULFILLED
        FULFILLED
        CANCELLED
    """

    if document.direction != TradeDirection.SALE:
        raise SalesOrderTypeError(
            "Only sale trade documents can be "
            "cancelled as sales orders"
        )

    if document.kind != TradeDocumentKind.ORDER:
        raise SalesOrderTypeError(
            "Only trade document kind 'order' can be "
            "cancelled as a sales order"
        )

    if document.status not in (
        TradeDocumentStatus.DRAFT,
        TradeDocumentStatus.CONFIRMED,
    ):
        raise SalesOrderStatusError(
            "Only draft or confirmed sales orders "
            "can be cancelled"
        )

    # A draft can be cancelled even if its structure became
    # incomplete. No reservation needs to be released.
    if document.status == TradeDocumentStatus.DRAFT:
        return

    if not document.lines:
        raise SalesOrderLinesRequiredError(
            "Confirmed sales order must contain "
            "at least one line"
        )

    missing_warehouse_lines = [
        line.line_number
        for line in document.lines
        if line.warehouse_id is None
    ]

    if missing_warehouse_lines:
        raise SalesOrderWarehouseRequiredError(
            "Confirmed sales order has lines without "
            "a reservation warehouse: "
            + ", ".join(
                str(line_number)
                for line_number
                in sorted(
                    missing_warehouse_lines
                )
            )
        )


def cancellation_release_order(
    document: TradeDocument,
) -> tuple[TradeDocumentLine, ...]:
    """
    Return confirmed-order lines in deterministic stock-lock order.

    Draft cancellation requires no reservation releases.
    """

    validate_sales_order_cancellation(
        document
    )

    if document.status == TradeDocumentStatus.DRAFT:
        return ()

    return tuple(
        sorted(
            document.lines,
            key=lambda line: (
                line.product_id,
                line.warehouse_id,
                line.id or 0,
            ),
        )
    )


async def cancel_sales_order(
    db: AsyncSession,
    *,
    company_id: int,
    document_id: int,
) -> TradeDocument:
    """
    Cancel one Sales Order atomically.

    Caller owns COMMIT / ROLLBACK.

    DRAFT:
        set CANCELLED
        no reservation movement

    CONFIRMED:
        calculate outstanding reservation per source line
        append RELEASE for every positive outstanding balance
        set CANCELLED

    PARTIALLY_FULFILLED and FULFILLED are intentionally rejected.
    They require fulfillment reversal / return semantics rather
    than a simple cancellation.
    """

    document = await get_locked_trade_document(
        db,
        company_id=company_id,
        document_id=document_id,
    )

    lines = cancellation_release_order(
        document
    )

    for line in lines:
        reserved_quantity = (
            await get_reserved_quantity_for_source_line(
                db,
                company_id=company_id,
                source_document_id=document.id,
                source_document_line_id=line.id,
            )
        )

        if reserved_quantity < 0:
            raise SalesOrderReservationStateError(
                "Sales order line has a negative "
                "reservation balance: "
                f"line_id={line.id}, "
                f"reserved_quantity={reserved_quantity}"
            )

        if reserved_quantity == 0:
            continue

        await release_source_line(
            db,
            company_id=company_id,
            source_document_id=document.id,
            source_document_line_id=line.id,
            quantity=reserved_quantity,
        )

    document.status = (
        TradeDocumentStatus.CANCELLED
    )

    document.cancelled_at = datetime.now(
        timezone.utc
    )

    await db.flush()

    return document
