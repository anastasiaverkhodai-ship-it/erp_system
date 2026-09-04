from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.document import (
    Document,
    DocumentStatus,
    DocumentType,
)
from app.models.document_line import DocumentLine
from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.trade_document import TradeDocument
from app.models.trade_document_line import TradeDocumentLine
from app.models.trade_fulfillment import TradeFulfillment
from app.models.trade_fulfillment_line import (
    TradeFulfillmentLine,
)
from app.services.invoice_fulfillment_allocation_types import (
    InvoiceFulfillmentAllocationStatus,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)

from app.services.input_vat_fulfillment_bridge_lifecycle_service import (
    InputVatFulfillmentBridgeLifecycleError,
    reconcile_input_vat_fulfillment_bridge_lifecycle_for_invoice_line,
)
from app.services.sales_recognition_lifecycle_service import (
    SalesRecognitionLifecycleError,
    reconcile_sales_recognition_lifecycle_for_invoice_line,
)
from app.services.tax_recognition_lifecycle_service import (
    TaxRecognitionLifecycleError,
    reconcile_tax_for_invoice_line,
)

from app.services.supplier_advance_clearing_lifecycle_service import (
    SupplierAdvanceClearingLifecycleError,
    reconcile_supplier_advance_clearing_lifecycle_for_invoice,
)

from app.services.customer_advance_clearing_lifecycle_service import (
    CustomerAdvanceClearingLifecycleError,
    reconcile_customer_advance_clearing_lifecycle_for_invoice,
)


ZERO = Decimal("0")


class InvoiceFulfillmentAllocationError(Exception):
    """Base Invoice <-> Fulfillment matching error."""


class InvoiceFulfillmentAllocationNotFoundError(
    InvoiceFulfillmentAllocationError
):
    """Required matching source object does not exist."""


class InvoiceFulfillmentAllocationTypeError(
    InvoiceFulfillmentAllocationError
):
    """Invoice/order document type is invalid."""


class InvoiceFulfillmentAllocationStatusError(
    InvoiceFulfillmentAllocationError
):
    """Invoice/order/warehouse document state is invalid."""


class InvoiceFulfillmentAllocationDirectionError(
    InvoiceFulfillmentAllocationError
):
    """Invoice and order directions differ."""


class InvoiceFulfillmentAllocationCounterpartyError(
    InvoiceFulfillmentAllocationError
):
    """Invoice and order counterparties differ."""


class InvoiceFulfillmentAllocationContractError(
    InvoiceFulfillmentAllocationError
):
    """Invoice and order contracts differ."""


class InvoiceFulfillmentAllocationCurrencyError(
    InvoiceFulfillmentAllocationError
):
    """Invoice and order currencies differ."""


class InvoiceFulfillmentAllocationProductError(
    InvoiceFulfillmentAllocationError
):
    """Invoice and fulfillment products differ."""


class InvoiceFulfillmentAllocationWarehouseError(
    InvoiceFulfillmentAllocationError
):
    """Fulfillment warehouse target is invalid."""


class InvoiceFulfillmentAllocationQuantityError(
    InvoiceFulfillmentAllocationError
):
    """Requested allocation quantity is invalid."""


class InvoiceOverAllocationError(
    InvoiceFulfillmentAllocationError
):
    """Allocation exceeds remaining Invoice line quantity."""


class FulfillmentOverAllocationError(
    InvoiceFulfillmentAllocationError
):
    """Allocation exceeds remaining fulfillment quantity."""


class DuplicateActiveInvoiceFulfillmentAllocationError(
    InvoiceFulfillmentAllocationError
):
    """The exact Invoice/Fulfillment line pair is already active."""


@dataclass(
    frozen=True,
    slots=True,
)
class InvoiceFulfillmentMatchContext:
    invoice: TradeDocument
    invoice_line: TradeDocumentLine

    order: TradeDocument
    fulfillment: TradeFulfillment
    fulfillment_line: TradeFulfillmentLine

    warehouse_document: Document
    warehouse_line: DocumentLine


@dataclass(
    frozen=True,
    slots=True,
)
class InvoiceFulfillmentAllocationPlan:
    quantity: Decimal

    invoice_allocated_before: Decimal
    invoice_allocated_after: Decimal

    fulfillment_allocated_before: Decimal
    fulfillment_allocated_after: Decimal


def get_expected_fulfillment_document_type(
    direction: TradeDirection,
) -> DocumentType:
    if direction == TradeDirection.SALE:
        return DocumentType.ISSUE

    if direction == TradeDirection.PURCHASE:
        return DocumentType.RECEIPT

    raise InvoiceFulfillmentAllocationDirectionError(
        "Unsupported Trade Invoice direction"
    )


def validate_invoice_fulfillment_match(
    context: InvoiceFulfillmentMatchContext,
) -> None:
    invoice = context.invoice
    invoice_line = context.invoice_line

    order = context.order
    fulfillment = context.fulfillment
    fulfillment_line = context.fulfillment_line

    warehouse_document = context.warehouse_document
    warehouse_line = context.warehouse_line

    if invoice.kind != TradeDocumentKind.INVOICE:
        raise InvoiceFulfillmentAllocationTypeError(
            "Matching source must be a Trade Invoice"
        )

    if invoice.status != TradeDocumentStatus.CONFIRMED:
        raise InvoiceFulfillmentAllocationStatusError(
            "Only confirmed Trade Invoice can be matched"
        )

    if order.kind != TradeDocumentKind.ORDER:
        raise InvoiceFulfillmentAllocationTypeError(
            "Fulfillment source must belong to a Trade Order"
        )

    if order.status not in (
        TradeDocumentStatus.PARTIALLY_FULFILLED,
        TradeDocumentStatus.FULFILLED,
    ):
        raise InvoiceFulfillmentAllocationStatusError(
            "Only partially fulfilled or fulfilled Trade Order "
            "can be matched to an Invoice"
        )

    if invoice.direction != order.direction:
        raise InvoiceFulfillmentAllocationDirectionError(
            "Invoice and fulfillment order directions differ"
        )

    if invoice.counterparty_id != order.counterparty_id:
        raise InvoiceFulfillmentAllocationCounterpartyError(
            "Invoice and fulfillment order counterparties differ"
        )

    if invoice.contract_id != order.contract_id:
        raise InvoiceFulfillmentAllocationContractError(
            "Invoice and fulfillment order contracts differ"
        )

    if (
        invoice.currency_code.upper()
        != order.currency_code.upper()
    ):
        raise InvoiceFulfillmentAllocationCurrencyError(
            "Invoice and fulfillment order currencies differ"
        )

    if (
        invoice_line.product_id
        != fulfillment_line.product_id
    ):
        raise InvoiceFulfillmentAllocationProductError(
            "Invoice line product does not match "
            "fulfillment line product"
        )

    if (
        invoice_line.warehouse_id is not None
        and invoice_line.warehouse_id
        != fulfillment_line.warehouse_id
    ):
        raise InvoiceFulfillmentAllocationWarehouseError(
            "Invoice line warehouse does not match "
            "fulfillment line warehouse"
        )

    expected_document_type = (
        get_expected_fulfillment_document_type(
            invoice.direction
        )
    )

    if (
        fulfillment.warehouse_document_type
        != expected_document_type.value
    ):
        raise InvoiceFulfillmentAllocationWarehouseError(
            "Fulfillment target document type does not match "
            "Trade direction"
        )

    if (
        warehouse_document.document_type
        != expected_document_type
    ):
        raise InvoiceFulfillmentAllocationWarehouseError(
            "Warehouse document type does not match "
            "Trade direction"
        )

    if (
        warehouse_document.status
        != DocumentStatus.POSTED
    ):
        raise InvoiceFulfillmentAllocationStatusError(
            "Only POSTED fulfillment can be matched "
            "to an Invoice"
        )

    if (
        warehouse_line.product_id
        != fulfillment_line.product_id
    ):
        raise InvoiceFulfillmentAllocationProductError(
            "Warehouse fulfillment line product mismatch"
        )

    if (
        warehouse_line.quantity
        != fulfillment_line.quantity
    ):
        raise InvoiceFulfillmentAllocationWarehouseError(
            "Warehouse fulfillment quantity does not match "
            "persistent fulfillment quantity"
        )

    if invoice_line.quantity <= ZERO:
        raise InvoiceFulfillmentAllocationQuantityError(
            "Invoice line quantity must be greater than zero"
        )

    if fulfillment_line.quantity <= ZERO:
        raise InvoiceFulfillmentAllocationQuantityError(
            "Fulfillment line quantity must be greater than zero"
        )


def create_invoice_fulfillment_allocation_plan(
    *,
    invoice_line_quantity: Decimal,
    fulfillment_line_quantity: Decimal,
    requested_quantity: Decimal,
    invoice_allocated_before: Decimal,
    fulfillment_allocated_before: Decimal,
) -> InvoiceFulfillmentAllocationPlan:
    invoice_line_quantity = Decimal(
        invoice_line_quantity
    )
    fulfillment_line_quantity = Decimal(
        fulfillment_line_quantity
    )
    requested_quantity = Decimal(
        requested_quantity
    )
    invoice_allocated_before = Decimal(
        invoice_allocated_before
    )
    fulfillment_allocated_before = Decimal(
        fulfillment_allocated_before
    )

    if requested_quantity <= ZERO:
        raise InvoiceFulfillmentAllocationQuantityError(
            "Allocation quantity must be greater than zero"
        )

    if invoice_allocated_before < ZERO:
        raise InvoiceFulfillmentAllocationQuantityError(
            "Persisted Invoice allocation cannot be negative"
        )

    if fulfillment_allocated_before < ZERO:
        raise InvoiceFulfillmentAllocationQuantityError(
            "Persisted fulfillment allocation cannot be negative"
        )

    if (
        invoice_allocated_before
        > invoice_line_quantity
    ):
        raise InvoiceOverAllocationError(
            "Persisted Invoice allocation already exceeds "
            "Invoice line quantity"
        )

    if (
        fulfillment_allocated_before
        > fulfillment_line_quantity
    ):
        raise FulfillmentOverAllocationError(
            "Persisted fulfillment allocation already exceeds "
            "fulfillment line quantity"
        )

    invoice_allocated_after = (
        invoice_allocated_before
        + requested_quantity
    )

    fulfillment_allocated_after = (
        fulfillment_allocated_before
        + requested_quantity
    )

    if (
        invoice_allocated_after
        > invoice_line_quantity
    ):
        raise InvoiceOverAllocationError(
            "Requested allocation exceeds remaining "
            "Invoice line quantity"
        )

    if (
        fulfillment_allocated_after
        > fulfillment_line_quantity
    ):
        raise FulfillmentOverAllocationError(
            "Requested allocation exceeds remaining "
            "fulfillment line quantity"
        )

    return InvoiceFulfillmentAllocationPlan(
        quantity=requested_quantity,
        invoice_allocated_before=(
            invoice_allocated_before
        ),
        invoice_allocated_after=(
            invoice_allocated_after
        ),
        fulfillment_allocated_before=(
            fulfillment_allocated_before
        ),
        fulfillment_allocated_after=(
            fulfillment_allocated_after
        ),
    )


async def _get_locked_invoice(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
) -> TradeDocument:
    result = await db.execute(
        select(
            TradeDocument
        )
        .where(
            TradeDocument.company_id
            == company_id,
            TradeDocument.id
            == invoice_id,
        )
        .with_for_update()
    )

    invoice = (
        result.scalar_one_or_none()
    )

    if invoice is None:
        raise InvoiceFulfillmentAllocationNotFoundError(
            "Trade Invoice not found"
        )

    return invoice


async def _get_locked_invoice_line(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
    invoice_line_id: int,
) -> TradeDocumentLine:
    result = await db.execute(
        select(
            TradeDocumentLine
        )
        .where(
            TradeDocumentLine.company_id
            == company_id,
            TradeDocumentLine.trade_document_id
            == invoice_id,
            TradeDocumentLine.id
            == invoice_line_id,
        )
        .with_for_update()
    )

    invoice_line = (
        result.scalar_one_or_none()
    )

    if invoice_line is None:
        raise InvoiceFulfillmentAllocationNotFoundError(
            "Trade Invoice line not found"
        )

    return invoice_line


async def _get_fulfillment_identity(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_id: int,
) -> TradeFulfillment:
    result = await db.execute(
        select(
            TradeFulfillment
        )
        .where(
            TradeFulfillment.company_id
            == company_id,
            TradeFulfillment.id
            == fulfillment_id,
        )
    )

    fulfillment = (
        result.scalar_one_or_none()
    )

    if fulfillment is None:
        raise InvoiceFulfillmentAllocationNotFoundError(
            "Trade Fulfillment not found"
        )

    return fulfillment


async def _get_locked_order(
    db: AsyncSession,
    *,
    company_id: int,
    order_id: int,
) -> TradeDocument:
    result = await db.execute(
        select(
            TradeDocument
        )
        .where(
            TradeDocument.company_id
            == company_id,
            TradeDocument.id
            == order_id,
        )
        .with_for_update()
    )

    order = result.scalar_one_or_none()

    if order is None:
        raise InvoiceFulfillmentAllocationNotFoundError(
            "Trade Order not found"
        )

    return order


async def _get_locked_fulfillment(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_id: int,
) -> TradeFulfillment:
    result = await db.execute(
        select(
            TradeFulfillment
        )
        .where(
            TradeFulfillment.company_id
            == company_id,
            TradeFulfillment.id
            == fulfillment_id,
        )
        .with_for_update()
    )

    fulfillment = (
        result.scalar_one_or_none()
    )

    if fulfillment is None:
        raise InvoiceFulfillmentAllocationNotFoundError(
            "Trade Fulfillment not found"
        )

    return fulfillment


async def _get_locked_fulfillment_line(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_id: int,
    fulfillment_line_id: int,
) -> TradeFulfillmentLine:
    result = await db.execute(
        select(
            TradeFulfillmentLine
        )
        .where(
            TradeFulfillmentLine.company_id
            == company_id,
            TradeFulfillmentLine.fulfillment_id
            == fulfillment_id,
            TradeFulfillmentLine.id
            == fulfillment_line_id,
        )
        .with_for_update()
    )

    fulfillment_line = (
        result.scalar_one_or_none()
    )

    if fulfillment_line is None:
        raise InvoiceFulfillmentAllocationNotFoundError(
            "Trade Fulfillment line not found"
        )

    return fulfillment_line


async def _get_locked_warehouse_document(
    db: AsyncSession,
    *,
    company_id: int,
    warehouse_document_id: int,
) -> Document:
    result = await db.execute(
        select(
            Document
        )
        .where(
            Document.company_id
            == company_id,
            Document.id
            == warehouse_document_id,
        )
        .with_for_update()
    )

    document = result.scalar_one_or_none()

    if document is None:
        raise InvoiceFulfillmentAllocationNotFoundError(
            "Warehouse fulfillment document not found"
        )

    return document


async def _get_locked_warehouse_line(
    db: AsyncSession,
    *,
    warehouse_document_id: int,
    warehouse_document_line_id: int,
) -> DocumentLine:
    result = await db.execute(
        select(
            DocumentLine
        )
        .where(
            DocumentLine.document_id
            == warehouse_document_id,
            DocumentLine.id
            == warehouse_document_line_id,
        )
        .with_for_update()
    )

    line = result.scalar_one_or_none()

    if line is None:
        raise InvoiceFulfillmentAllocationNotFoundError(
            "Warehouse fulfillment line not found"
        )

    return line


async def get_active_invoice_allocated_quantity(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_line_id: int,
) -> Decimal:
    result = await db.execute(
        select(
            func.coalesce(
                func.sum(
                    InvoiceFulfillmentAllocation.quantity
                ),
                ZERO,
            )
        )
        .where(
            InvoiceFulfillmentAllocation.company_id
            == company_id,
            InvoiceFulfillmentAllocation.invoice_line_id
            == invoice_line_id,
            InvoiceFulfillmentAllocation.status
            == InvoiceFulfillmentAllocationStatus.ACTIVE,
        )
    )

    return Decimal(
        result.scalar_one()
    )


async def get_active_fulfillment_allocated_quantity(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_line_id: int,
) -> Decimal:
    result = await db.execute(
        select(
            func.coalesce(
                func.sum(
                    InvoiceFulfillmentAllocation.quantity
                ),
                ZERO,
            )
        )
        .where(
            InvoiceFulfillmentAllocation.company_id
            == company_id,
            InvoiceFulfillmentAllocation.fulfillment_line_id
            == fulfillment_line_id,
            InvoiceFulfillmentAllocation.status
            == InvoiceFulfillmentAllocationStatus.ACTIVE,
        )
    )

    return Decimal(
        result.scalar_one()
    )


async def _active_pair_exists(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_line_id: int,
    fulfillment_line_id: int,
) -> bool:
    result = await db.execute(
        select(
            InvoiceFulfillmentAllocation.id
        )
        .where(
            InvoiceFulfillmentAllocation.company_id
            == company_id,
            InvoiceFulfillmentAllocation.invoice_line_id
            == invoice_line_id,
            InvoiceFulfillmentAllocation.fulfillment_line_id
            == fulfillment_line_id,
            InvoiceFulfillmentAllocation.status
            == InvoiceFulfillmentAllocationStatus.ACTIVE,
        )
        .limit(1)
    )

    return (
        result.scalar_one_or_none()
        is not None
    )


async def create_invoice_fulfillment_allocation(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
    invoice_line_id: int,
    fulfillment_id: int,
    fulfillment_line_id: int,
    quantity: Decimal,
    created_by: int,
) -> InvoiceFulfillmentAllocation:
    """
    Create one ACTIVE Invoice <-> Fulfillment allocation.

    Caller owns COMMIT / ROLLBACK.

    Lock order:
        Invoice header
        Invoice line
        Order header
        Fulfillment header
        Fulfillment line
        Warehouse document
        Warehouse line

    This lock order must also be respected by future matching
    reversal / fulfillment-reversal guards.
    """

    if created_by <= 0:
        raise InvoiceFulfillmentAllocationError(
            "created_by must be greater than zero"
        )

    invoice = await _get_locked_invoice(
        db,
        company_id=company_id,
        invoice_id=invoice_id,
    )

    invoice_line = await _get_locked_invoice_line(
        db,
        company_id=company_id,
        invoice_id=invoice_id,
        invoice_line_id=invoice_line_id,
    )

    fulfillment_identity = (
        await _get_fulfillment_identity(
            db,
            company_id=company_id,
            fulfillment_id=fulfillment_id,
        )
    )

    order = await _get_locked_order(
        db,
        company_id=company_id,
        order_id=(
            fulfillment_identity.trade_document_id
        ),
    )

    fulfillment = await _get_locked_fulfillment(
        db,
        company_id=company_id,
        fulfillment_id=fulfillment_id,
    )

    if (
        fulfillment.trade_document_id
        != order.id
    ):
        raise InvoiceFulfillmentAllocationError(
            "Trade Fulfillment source order changed "
            "during matching"
        )

    fulfillment_line = (
        await _get_locked_fulfillment_line(
            db,
            company_id=company_id,
            fulfillment_id=fulfillment_id,
            fulfillment_line_id=(
                fulfillment_line_id
            ),
        )
    )

    warehouse_document = (
        await _get_locked_warehouse_document(
            db,
            company_id=company_id,
            warehouse_document_id=(
                fulfillment.warehouse_document_id
            ),
        )
    )

    warehouse_line = (
        await _get_locked_warehouse_line(
            db,
            warehouse_document_id=(
                fulfillment_line.warehouse_document_id
            ),
            warehouse_document_line_id=(
                fulfillment_line
                .warehouse_document_line_id
            ),
        )
    )

    context = InvoiceFulfillmentMatchContext(
        invoice=invoice,
        invoice_line=invoice_line,
        order=order,
        fulfillment=fulfillment,
        fulfillment_line=fulfillment_line,
        warehouse_document=warehouse_document,
        warehouse_line=warehouse_line,
    )

    validate_invoice_fulfillment_match(
        context
    )

    if (
        fulfillment_line.trade_document_id
        != order.id
    ):
        raise InvoiceFulfillmentAllocationError(
            "Fulfillment line does not belong "
            "to the locked Trade Order"
        )

    if (
        fulfillment_line.warehouse_document_id
        != warehouse_document.id
    ):
        raise InvoiceFulfillmentAllocationWarehouseError(
            "Fulfillment line warehouse document mismatch"
        )

    if await _active_pair_exists(
        db,
        company_id=company_id,
        invoice_line_id=invoice_line.id,
        fulfillment_line_id=(
            fulfillment_line.id
        ),
    ):
        raise (
            DuplicateActiveInvoiceFulfillmentAllocationError(
                "This Invoice line and Fulfillment line "
                "already have an ACTIVE allocation"
            )
        )

    invoice_allocated_before = (
        await get_active_invoice_allocated_quantity(
            db,
            company_id=company_id,
            invoice_line_id=invoice_line.id,
        )
    )

    fulfillment_allocated_before = (
        await get_active_fulfillment_allocated_quantity(
            db,
            company_id=company_id,
            fulfillment_line_id=(
                fulfillment_line.id
            ),
        )
    )

    plan = (
        create_invoice_fulfillment_allocation_plan(
            invoice_line_quantity=(
                invoice_line.quantity
            ),
            fulfillment_line_quantity=(
                fulfillment_line.quantity
            ),
            requested_quantity=quantity,
            invoice_allocated_before=(
                invoice_allocated_before
            ),
            fulfillment_allocated_before=(
                fulfillment_allocated_before
            ),
        )
    )

    allocation = InvoiceFulfillmentAllocation(
        company_id=company_id,
        invoice_id=invoice.id,
        invoice_line_id=invoice_line.id,
        fulfillment_id=fulfillment.id,
        fulfillment_line_id=(
            fulfillment_line.id
        ),
        order_id=order.id,
        order_line_id=(
            fulfillment_line.trade_document_line_id
        ),
        product_id=invoice_line.product_id,
        quantity=plan.quantity,
        status=(
            InvoiceFulfillmentAllocationStatus.ACTIVE
        ),
        created_by=created_by,
    )

    db.add(
        allocation
    )

    await db.flush()

    adjustment_date = (
        datetime.now(
            timezone.utc
        ).date()
    )

    try:
        await (
            reconcile_sales_recognition_lifecycle_for_invoice_line(
                db,
                company_id=company_id,
                invoice_id=invoice.id,
                invoice_line_id=invoice_line.id,
                adjustment_date=adjustment_date,
                created_by=created_by,
            )
        )
    except SalesRecognitionLifecycleError as exc:
        raise InvoiceFulfillmentAllocationError(
            "Sales recognition reconciliation "
            "failed: "
            f"{exc}"
        ) from exc

    # Sales Recognition owns economic 361.
    # Customer clearing is therefore safe only after the
    # SalesRecognitionEvent lifecycle has reached its new state.
    if (
        invoice.direction
        == TradeDirection.SALE
    ):
        try:
            await (
                reconcile_customer_advance_clearing_lifecycle_for_invoice(
                    db,
                    company_id=company_id,
                    invoice_id=invoice.id,
                    adjustment_date=(
                        adjustment_date
                    ),
                    created_by=created_by,
                )
            )
        except CustomerAdvanceClearingLifecycleError as exc:
            raise InvoiceFulfillmentAllocationError(
                "Customer advance clearing "
                "lifecycle failed: "
                f"{exc}"
            ) from exc

    try:
        await (
            reconcile_input_vat_fulfillment_bridge_lifecycle_for_invoice_line(
                db,
                company_id=company_id,
                invoice_id=invoice.id,
                invoice_line_id=invoice_line.id,
                adjustment_date=adjustment_date,
                created_by=created_by,
            )
        )
    except InputVatFulfillmentBridgeLifecycleError as exc:
        raise InvoiceFulfillmentAllocationError(
            "INPUT VAT fulfillment bridge "
            "reconciliation failed: "
            f"{exc}"
        ) from exc

    try:
        await reconcile_tax_for_invoice_line(
            db,
            company_id=company_id,
            invoice_id=invoice.id,
            invoice_line_id=invoice_line.id,
            adjustment_date=adjustment_date,
            created_by=created_by,
        )
    except TaxRecognitionLifecycleError as exc:
        raise InvoiceFulfillmentAllocationError(
            "VAT recognition reconciliation "
            "failed: "
            f"{exc}"
        ) from exc

    if (
        invoice.direction
        == TradeDirection.PURCHASE
    ):
        try:
            await (
                reconcile_supplier_advance_clearing_lifecycle_for_invoice(
                    db,
                    company_id=company_id,
                    invoice_id=invoice.id,
                    adjustment_date=(
                        adjustment_date
                    ),
                    created_by=created_by,
                )
            )
        except SupplierAdvanceClearingLifecycleError as exc:
            raise InvoiceFulfillmentAllocationError(
                "Supplier advance clearing "
                "lifecycle failed: "
                f"{exc}"
            ) from exc

    return allocation


# ============================================================
# STEP 15.4 - ALLOCATION REVERSAL AND ACTIVE-MATCH GUARDS
# ============================================================


class InvoiceFulfillmentAllocationReversalStateError(
    InvoiceFulfillmentAllocationError
):
    """Persistent allocation cannot be reversed in its state."""


async def has_active_invoice_allocations(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
    lock_rows: bool = False,
) -> bool:
    """
    Return whether an Invoice currently has at least one
    ACTIVE fulfillment allocation.

    When lock_rows=True, any matched ACTIVE row is locked.
    Invoice cancellation already owns the Invoice header lock,
    which serializes this check against new allocation creation.
    """

    statement = (
        select(
            InvoiceFulfillmentAllocation.id
        )
        .where(
            InvoiceFulfillmentAllocation.company_id
            == company_id,
            InvoiceFulfillmentAllocation.invoice_id
            == invoice_id,
            InvoiceFulfillmentAllocation.status
            == InvoiceFulfillmentAllocationStatus.ACTIVE,
        )
        .limit(1)
    )

    if lock_rows:
        statement = statement.with_for_update()

    result = await db.execute(
        statement
    )

    return (
        result.scalar_one_or_none()
        is not None
    )


async def has_active_fulfillment_allocations(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_id: int,
    lock_rows: bool = False,
) -> bool:
    """
    Return whether one persistent Trade Fulfillment currently
    participates in any ACTIVE Invoice allocation.

    Fulfillment reversal owns the Order/Fulfillment locks before
    calling this guard, preventing a new valid matching operation
    from racing through the reversal.
    """

    statement = (
        select(
            InvoiceFulfillmentAllocation.id
        )
        .where(
            InvoiceFulfillmentAllocation.company_id
            == company_id,
            InvoiceFulfillmentAllocation.fulfillment_id
            == fulfillment_id,
            InvoiceFulfillmentAllocation.status
            == InvoiceFulfillmentAllocationStatus.ACTIVE,
        )
        .limit(1)
    )

    if lock_rows:
        statement = statement.with_for_update()

    result = await db.execute(
        statement
    )

    return (
        result.scalar_one_or_none()
        is not None
    )


async def reverse_invoice_fulfillment_allocation(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
    allocation_id: int,
    reversed_by: int,
) -> InvoiceFulfillmentAllocation:
    """
    Reverse one persistent Invoice/Fulfillment allocation.

    Reversal is append-preserving:
        ACTIVE -> REVERSED

    Quantity and business identity snapshots are not changed.
    The row is never deleted.

    Caller owns COMMIT / ROLLBACK.
    """

    if invoice_id <= 0:
        raise InvoiceFulfillmentAllocationError(
            "invoice_id must be greater than zero"
        )

    if allocation_id <= 0:
        raise InvoiceFulfillmentAllocationError(
            "allocation_id must be greater than zero"
        )

    if reversed_by <= 0:
        raise InvoiceFulfillmentAllocationError(
            "reversed_by must be greater than zero"
        )

    # CREATE locks Invoice before matching sources.
    # Reversal follows the same leading lock order before it
    # mutates InvoiceFulfillmentAllocation.
    invoice = await _get_locked_invoice(
        db,
        company_id=company_id,
        invoice_id=invoice_id,
    )

    result = await db.execute(
        select(
            InvoiceFulfillmentAllocation
        )
        .where(
            InvoiceFulfillmentAllocation.company_id
            == company_id,
            InvoiceFulfillmentAllocation.invoice_id
            == invoice_id,
            InvoiceFulfillmentAllocation.id
            == allocation_id,
        )
        .with_for_update()
    )

    allocation = (
        result.scalar_one_or_none()
    )

    if allocation is None:
        raise InvoiceFulfillmentAllocationNotFoundError(
            "Invoice Fulfillment allocation not found"
        )

    if (
        allocation.status
        != InvoiceFulfillmentAllocationStatus.ACTIVE
    ):
        raise (
            InvoiceFulfillmentAllocationReversalStateError(
                "Only ACTIVE Invoice Fulfillment "
                "allocation can be reversed"
            )
        )

    allocation.status = (
        InvoiceFulfillmentAllocationStatus.REVERSED
    )

    allocation.reversed_by = reversed_by

    allocation.reversed_at = datetime.now(
        timezone.utc
    )

    await db.flush()

    adjustment_date = (
        allocation.reversed_at.date()
    )

    try:
        await (
            reconcile_sales_recognition_lifecycle_for_invoice_line(
                db,
                company_id=company_id,
                invoice_id=invoice_id,
                invoice_line_id=(
                    allocation.invoice_line_id
                ),
                adjustment_date=adjustment_date,
                created_by=reversed_by,
            )
        )
    except SalesRecognitionLifecycleError as exc:
        raise InvoiceFulfillmentAllocationError(
            "Sales recognition reconciliation "
            "failed: "
            f"{exc}"
        ) from exc

    # Sales Recognition owns economic 361.
    # Customer clearing is therefore safe only after the
    # SalesRecognitionEvent lifecycle has reached its new state.
    if (
        invoice.direction
        == TradeDirection.SALE
    ):
        try:
            await (
                reconcile_customer_advance_clearing_lifecycle_for_invoice(
                    db,
                    company_id=company_id,
                    invoice_id=invoice_id,
                    adjustment_date=(
                        adjustment_date
                    ),
                    created_by=reversed_by,
                )
            )
        except CustomerAdvanceClearingLifecycleError as exc:
            raise InvoiceFulfillmentAllocationError(
                "Customer advance clearing "
                "lifecycle failed: "
                f"{exc}"
            ) from exc

    try:
        await reconcile_tax_for_invoice_line(
            db,
            company_id=company_id,
            invoice_id=invoice_id,
            invoice_line_id=(
                allocation.invoice_line_id
            ),
            adjustment_date=adjustment_date,
            created_by=reversed_by,
        )
    except TaxRecognitionLifecycleError as exc:
        raise InvoiceFulfillmentAllocationError(
            "VAT recognition reconciliation "
            "failed: "
            f"{exc}"
        ) from exc

    try:
        await (
            reconcile_input_vat_fulfillment_bridge_lifecycle_for_invoice_line(
                db,
                company_id=company_id,
                invoice_id=invoice_id,
                invoice_line_id=(
                    allocation.invoice_line_id
                ),
                adjustment_date=adjustment_date,
                created_by=reversed_by,
            )
        )
    except InputVatFulfillmentBridgeLifecycleError as exc:
        raise InvoiceFulfillmentAllocationError(
            "INPUT VAT fulfillment bridge "
            "reconciliation failed: "
            f"{exc}"
        ) from exc

    # Economic INPUT VAT bridge has already reached its
    # final state above. Supplier liability reconstruction is
    # therefore safe now.
    if (
        invoice.direction
        == TradeDirection.PURCHASE
    ):
        try:
            await (
                reconcile_supplier_advance_clearing_lifecycle_for_invoice(
                    db,
                    company_id=company_id,
                    invoice_id=invoice_id,
                    adjustment_date=(
                        adjustment_date
                    ),
                    created_by=reversed_by,
                )
            )
        except SupplierAdvanceClearingLifecycleError as exc:
            raise InvoiceFulfillmentAllocationError(
                "Supplier advance clearing "
                "lifecycle failed: "
                f"{exc}"
            ) from exc

    return allocation


# ============================================================
# STEP 15.5 - RECONCILIATION READ MODEL
# ============================================================


@dataclass(
    frozen=True,
    slots=True,
)
class InvoiceFulfillmentReconciliationAllocation:
    allocation: InvoiceFulfillmentAllocation

    fulfillment_line_quantity: Decimal

    fulfillment_line_active_allocated_quantity: Decimal

    fulfillment_line_remaining_quantity: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class InvoiceFulfillmentReconciliationLine:
    invoice_line: TradeDocumentLine

    active_allocated_quantity: Decimal

    remaining_quantity: Decimal

    allocations: tuple[
        InvoiceFulfillmentReconciliationAllocation,
        ...,
    ]

    @property
    def fully_allocated(
        self,
    ) -> bool:
        return self.remaining_quantity == ZERO


@dataclass(
    frozen=True,
    slots=True,
)
class InvoiceFulfillmentReconciliation:
    invoice: TradeDocument

    lines: tuple[
        InvoiceFulfillmentReconciliationLine,
        ...,
    ]

    @property
    def fully_allocated(
        self,
    ) -> bool:
        return all(
            line.fully_allocated
            for line in self.lines
        )


async def get_invoice_fulfillment_allocation_history(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
) -> tuple[
    TradeDocument,
    tuple[
        InvoiceFulfillmentAllocation,
        ...,
    ],
]:
    """
    Load one company-scoped Trade Invoice plus complete
    Invoice/Fulfillment allocation history.

    Both ACTIVE and REVERSED rows are returned.
    """

    invoice = (
        await db.execute(
            select(
                TradeDocument
            )
            .where(
                TradeDocument.company_id
                == company_id,
                TradeDocument.id
                == invoice_id,
            )
        )
    ).scalar_one_or_none()

    if invoice is None:
        raise InvoiceFulfillmentAllocationNotFoundError(
            "Trade Invoice not found"
        )

    if invoice.kind != TradeDocumentKind.INVOICE:
        raise InvoiceFulfillmentAllocationTypeError(
            "Trade document must be an Invoice"
        )

    allocations = tuple(
        (
            await db.execute(
                select(
                    InvoiceFulfillmentAllocation
                )
                .where(
                    InvoiceFulfillmentAllocation.company_id
                    == company_id,
                    InvoiceFulfillmentAllocation.invoice_id
                    == invoice_id,
                )
                .order_by(
                    InvoiceFulfillmentAllocation.invoice_line_id,
                    InvoiceFulfillmentAllocation.id,
                )
            )
        ).scalars().all()
    )

    return (
        invoice,
        allocations,
    )


async def get_invoice_fulfillment_reconciliation(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
) -> InvoiceFulfillmentReconciliation:
    """
    Build quantity reconciliation for one Trade Invoice.

    This read model deliberately does not create or persist
    monetary allocation values.

    Quantity truth comes from:
      - TradeDocumentLine.quantity
      - InvoiceFulfillmentAllocation.quantity
      - TradeFulfillmentLine.quantity
    """

    invoice = (
        await db.execute(
            select(
                TradeDocument
            )
            .options(
                selectinload(
                    TradeDocument.lines
                )
            )
            .where(
                TradeDocument.company_id
                == company_id,
                TradeDocument.id
                == invoice_id,
            )
        )
    ).scalar_one_or_none()

    if invoice is None:
        raise InvoiceFulfillmentAllocationNotFoundError(
            "Trade Invoice not found"
        )

    if invoice.kind != TradeDocumentKind.INVOICE:
        raise InvoiceFulfillmentAllocationTypeError(
            "Trade document must be an Invoice"
        )

    allocations = tuple(
        (
            await db.execute(
                select(
                    InvoiceFulfillmentAllocation
                )
                .where(
                    InvoiceFulfillmentAllocation.company_id
                    == company_id,
                    InvoiceFulfillmentAllocation.invoice_id
                    == invoice_id,
                )
                .order_by(
                    InvoiceFulfillmentAllocation.invoice_line_id,
                    InvoiceFulfillmentAllocation.id,
                )
            )
        ).scalars().all()
    )

    fulfillment_line_ids = sorted(
        {
            allocation.fulfillment_line_id
            for allocation in allocations
        }
    )

    fulfillment_quantities: dict[
        int,
        Decimal,
    ] = {}

    active_fulfillment_allocations: dict[
        int,
        Decimal,
    ] = {}

    if fulfillment_line_ids:
        fulfillment_rows = (
            await db.execute(
                select(
                    TradeFulfillmentLine.id,
                    TradeFulfillmentLine.quantity,
                )
                .where(
                    TradeFulfillmentLine.company_id
                    == company_id,
                    TradeFulfillmentLine.id.in_(
                        fulfillment_line_ids
                    ),
                )
            )
        ).all()

        fulfillment_quantities = {
            row.id: Decimal(
                row.quantity
            )
            for row in fulfillment_rows
        }

        if (
            len(fulfillment_quantities)
            != len(fulfillment_line_ids)
        ):
            raise InvoiceFulfillmentAllocationError(
                "Persisted allocation references a missing "
                "Trade Fulfillment line"
            )

        active_rows = (
            await db.execute(
                select(
                    InvoiceFulfillmentAllocation.fulfillment_line_id,
                    func.sum(
                        InvoiceFulfillmentAllocation.quantity
                    ),
                )
                .where(
                    InvoiceFulfillmentAllocation.company_id
                    == company_id,
                    InvoiceFulfillmentAllocation.fulfillment_line_id.in_(
                        fulfillment_line_ids
                    ),
                    InvoiceFulfillmentAllocation.status
                    == InvoiceFulfillmentAllocationStatus.ACTIVE,
                )
                .group_by(
                    InvoiceFulfillmentAllocation.fulfillment_line_id
                )
            )
        ).all()

        active_fulfillment_allocations = {
            fulfillment_line_id: Decimal(
                quantity
            )
            for (
                fulfillment_line_id,
                quantity,
            ) in active_rows
        }

    allocations_by_invoice_line: dict[
        int,
        list[
            InvoiceFulfillmentAllocation
        ],
    ] = {}

    for allocation in allocations:
        allocations_by_invoice_line.setdefault(
            allocation.invoice_line_id,
            [],
        ).append(
            allocation
        )

    reconciliation_lines = []

    for invoice_line in sorted(
        invoice.lines,
        key=lambda item: (
            item.line_number,
            item.id,
        ),
    ):
        line_allocations = tuple(
            allocations_by_invoice_line.get(
                invoice_line.id,
                (),
            )
        )

        active_allocated_quantity = sum(
            (
                Decimal(
                    allocation.quantity
                )
                for allocation
                in line_allocations
                if (
                    allocation.status
                    == InvoiceFulfillmentAllocationStatus.ACTIVE
                )
            ),
            ZERO,
        )

        invoice_quantity = Decimal(
            invoice_line.quantity
        )

        if (
            active_allocated_quantity
            > invoice_quantity
        ):
            raise InvoiceOverAllocationError(
                "Persisted ACTIVE Invoice allocation exceeds "
                "Invoice line quantity"
            )

        remaining_quantity = (
            invoice_quantity
            - active_allocated_quantity
        )

        reconciliation_allocations = []

        for allocation in line_allocations:
            fulfillment_line_quantity = (
                fulfillment_quantities[
                    allocation.fulfillment_line_id
                ]
            )

            fulfillment_active_quantity = (
                active_fulfillment_allocations.get(
                    allocation.fulfillment_line_id,
                    ZERO,
                )
            )

            if (
                fulfillment_active_quantity
                > fulfillment_line_quantity
            ):
                raise FulfillmentOverAllocationError(
                    "Persisted ACTIVE allocation exceeds "
                    "Trade Fulfillment line quantity"
                )

            reconciliation_allocations.append(
                InvoiceFulfillmentReconciliationAllocation(
                    allocation=allocation,
                    fulfillment_line_quantity=(
                        fulfillment_line_quantity
                    ),
                    fulfillment_line_active_allocated_quantity=(
                        fulfillment_active_quantity
                    ),
                    fulfillment_line_remaining_quantity=(
                        fulfillment_line_quantity
                        - fulfillment_active_quantity
                    ),
                )
            )

        reconciliation_lines.append(
            InvoiceFulfillmentReconciliationLine(
                invoice_line=invoice_line,
                active_allocated_quantity=(
                    active_allocated_quantity
                ),
                remaining_quantity=(
                    remaining_quantity
                ),
                allocations=tuple(
                    reconciliation_allocations
                ),
            )
        )

    return InvoiceFulfillmentReconciliation(
        invoice=invoice,
        lines=tuple(
            reconciliation_lines
        ),
    )
