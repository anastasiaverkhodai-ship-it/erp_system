from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import (
    Document,
    DocumentStatus,
    DocumentType,
)
from app.models.document_line import DocumentLine
from app.models.journal_entry import JournalEntry
from app.models.trade_document import TradeDocument
from app.models.trade_document_line import TradeDocumentLine
from app.models.trade_fulfillment import (
    TradeFulfillment,
)
from app.models.trade_fulfillment_line import (
    TradeFulfillmentLine,
)
from app.services.reservation_persistence_service import (
    consume_source_line_reservation,
    get_reserved_quantity_for_source_line,
)
from app.services.document_posting import (
    post_document,
)
from app.services.trade_document_lifecycle_service import (
    get_locked_trade_document,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)


ZERO = Decimal("0")


class SalesOrderFulfillmentError(Exception):
    """Base error for sales-order fulfillment."""


class SalesOrderFulfillmentTypeError(
    SalesOrderFulfillmentError
):
    """Trade document is not a Sales Order."""


class SalesOrderFulfillmentStatusError(
    SalesOrderFulfillmentError
):
    """Sales Order cannot be fulfilled in its current state."""


class SalesOrderFulfillmentLinesRequiredError(
    SalesOrderFulfillmentError
):
    """Sales Order has no source lines."""


class SalesOrderFulfillmentRequestRequiredError(
    SalesOrderFulfillmentError
):
    """Fulfillment request contains no lines."""


class SalesOrderFulfillmentDuplicateLineError(
    SalesOrderFulfillmentError
):
    """The same source line appears more than once."""


class SalesOrderFulfillmentSourceLineNotFoundError(
    SalesOrderFulfillmentError
):
    """Requested source line does not belong to the Sales Order."""


class SalesOrderFulfillmentWarehouseRequiredError(
    SalesOrderFulfillmentError
):
    """Source line does not have a reservation warehouse."""


class SalesOrderFulfillmentStateError(
    SalesOrderFulfillmentError
):
    """Stored fulfillment state is internally invalid."""


class SalesOrderOverFulfillmentError(
    SalesOrderFulfillmentError
):
    """Requested quantity exceeds remaining source quantity."""


class SalesOrderFulfillmentReservationStateError(
    SalesOrderFulfillmentError
):
    """Reservation state does not match fulfillment state."""


class SalesOrderInsufficientReservationError(
    SalesOrderFulfillmentError
):
    """Requested fulfillment exceeds outstanding reservation."""


@dataclass(
    frozen=True,
    slots=True,
)
class SalesOrderFulfillmentRequestLine:
    trade_document_line_id: int
    quantity: Decimal

    def __post_init__(self) -> None:
        if self.trade_document_line_id <= 0:
            raise ValueError(
                "Trade document line ID must be "
                "greater than zero"
            )

        quantity = Decimal(
            self.quantity
        )

        if quantity <= ZERO:
            raise ValueError(
                "Fulfillment quantity must be "
                "greater than zero"
            )

        object.__setattr__(
            self,
            "quantity",
            quantity,
        )


@dataclass(
    frozen=True,
    slots=True,
)
class SalesOrderFulfillmentPlanLine:
    source_line: TradeDocumentLine
    quantity: Decimal
    fulfilled_before: Decimal
    fulfilled_after: Decimal
    reserved_before: Decimal
    reserved_after: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class SalesOrderFulfillmentPlan:
    lines: tuple[
        SalesOrderFulfillmentPlanLine,
        ...,
    ]
    resulting_status: TradeDocumentStatus


def validate_sales_order_fulfillment_state(
    document: TradeDocument,
) -> None:
    """
    Fulfillment is allowed only for an already confirmed
    Sales Order.

    PARTIALLY_FULFILLED may be fulfilled again.

    DRAFT, FULFILLED, and CANCELLED are rejected.
    """

    if document.direction != TradeDirection.SALE:
        raise SalesOrderFulfillmentTypeError(
            "Only sale trade documents can be "
            "fulfilled as sales orders"
        )

    if document.kind != TradeDocumentKind.ORDER:
        raise SalesOrderFulfillmentTypeError(
            "Only trade document kind 'order' can be "
            "fulfilled as a sales order"
        )

    if document.status not in (
        TradeDocumentStatus.CONFIRMED,
        TradeDocumentStatus.PARTIALLY_FULFILLED,
    ):
        raise SalesOrderFulfillmentStatusError(
            "Only confirmed or partially fulfilled "
            "sales orders can be fulfilled"
        )

    if not document.lines:
        raise SalesOrderFulfillmentLinesRequiredError(
            "Sales order must contain at least one line"
        )

    missing_warehouse_lines = [
        line.line_number
        for line in document.lines
        if line.warehouse_id is None
    ]

    if missing_warehouse_lines:
        raise SalesOrderFulfillmentWarehouseRequiredError(
            "Sales order has source lines without "
            "a reservation warehouse: "
            + ", ".join(
                str(line_number)
                for line_number
                in sorted(
                    missing_warehouse_lines
                )
            )
        )


def calculate_sales_order_fulfillment_status(
    document: TradeDocument,
    fulfilled_quantities: Mapping[
        int,
        Decimal,
    ],
) -> TradeDocumentStatus:
    """
    Calculate lifecycle status from persistent fulfilled
    quantities.

    Fulfilled quantity means quantity represented by posted
    warehouse ISSUE documents.
    """

    if not document.lines:
        raise SalesOrderFulfillmentLinesRequiredError(
            "Sales order must contain at least one line"
        )

    any_fulfilled = False
    all_fulfilled = True

    for line in document.lines:
        if line.id is None or line.id <= 0:
            raise SalesOrderFulfillmentStateError(
                "Sales order contains an unpersisted "
                "source line"
            )

        source_quantity = Decimal(
            line.quantity
        )

        fulfilled_quantity = Decimal(
            fulfilled_quantities.get(
                line.id,
                ZERO,
            )
        )

        if fulfilled_quantity < ZERO:
            raise SalesOrderFulfillmentStateError(
                "Fulfilled quantity cannot be negative: "
                f"line_id={line.id}, "
                f"fulfilled={fulfilled_quantity}"
            )

        if fulfilled_quantity > source_quantity:
            raise SalesOrderFulfillmentStateError(
                "Persisted fulfillment exceeds "
                "source quantity: "
                f"line_id={line.id}, "
                f"source={source_quantity}, "
                f"fulfilled={fulfilled_quantity}"
            )

        if fulfilled_quantity > ZERO:
            any_fulfilled = True

        if fulfilled_quantity != source_quantity:
            all_fulfilled = False

    if all_fulfilled:
        return TradeDocumentStatus.FULFILLED

    if any_fulfilled:
        return (
            TradeDocumentStatus.PARTIALLY_FULFILLED
        )

    return TradeDocumentStatus.CONFIRMED


def create_sales_order_fulfillment_plan(
    document: TradeDocument,
    request_lines: Sequence[
        SalesOrderFulfillmentRequestLine
    ],
    *,
    fulfilled_quantities: Mapping[
        int,
        Decimal,
    ],
    reserved_quantities: Mapping[
        int,
        Decimal,
    ],
) -> SalesOrderFulfillmentPlan:
    """
    Pure fulfillment planner.

    No database writes.

    Current invariant:

        outstanding reservation
        =
        source quantity - posted fulfilled quantity

    That invariant is valid because confirmation reserves the
    full order line and each successful fulfillment consumes
    exactly the quantity physically issued.
    """

    validate_sales_order_fulfillment_state(
        document
    )

    requests = tuple(
        request_lines
    )

    if not requests:
        raise (
            SalesOrderFulfillmentRequestRequiredError(
                "At least one fulfillment line "
                "is required"
            )
        )

    request_ids = [
        request.trade_document_line_id
        for request in requests
    ]

    if (
        len(request_ids)
        != len(set(request_ids))
    ):
        raise (
            SalesOrderFulfillmentDuplicateLineError(
                "Each sales-order source line may "
                "appear only once per fulfillment"
            )
        )

    source_lines: dict[
        int,
        TradeDocumentLine,
    ] = {}

    current_fulfilled: dict[
        int,
        Decimal,
    ] = {}

    current_reserved: dict[
        int,
        Decimal,
    ] = {}

    for line in document.lines:
        if line.id is None or line.id <= 0:
            raise SalesOrderFulfillmentStateError(
                "Sales order contains an unpersisted "
                "source line"
            )

        source_lines[line.id] = line

        source_quantity = Decimal(
            line.quantity
        )

        fulfilled_before = Decimal(
            fulfilled_quantities.get(
                line.id,
                ZERO,
            )
        )

        reserved_before = Decimal(
            reserved_quantities.get(
                line.id,
                ZERO,
            )
        )

        if fulfilled_before < ZERO:
            raise SalesOrderFulfillmentStateError(
                "Fulfilled quantity cannot be negative: "
                f"line_id={line.id}"
            )

        if fulfilled_before > source_quantity:
            raise SalesOrderFulfillmentStateError(
                "Persisted fulfillment exceeds "
                "source quantity: "
                f"line_id={line.id}"
            )

        if reserved_before < ZERO:
            raise (
                SalesOrderFulfillmentReservationStateError(
                    "Outstanding reservation cannot "
                    "be negative: "
                    f"line_id={line.id}"
                )
            )

        expected_reservation = (
            source_quantity
            - fulfilled_before
        )

        if (
            reserved_before
            != expected_reservation
        ):
            raise (
                SalesOrderFulfillmentReservationStateError(
                    "Reservation does not match "
                    "remaining fulfillment quantity: "
                    f"line_id={line.id}, "
                    f"expected={expected_reservation}, "
                    f"reserved={reserved_before}"
                )
            )

        current_fulfilled[
            line.id
        ] = fulfilled_before

        current_reserved[
            line.id
        ] = reserved_before

    plan_lines: list[
        SalesOrderFulfillmentPlanLine
    ] = []

    resulting_fulfilled = dict(
        current_fulfilled
    )

    for request in requests:
        line = source_lines.get(
            request.trade_document_line_id
        )

        if line is None:
            raise (
                SalesOrderFulfillmentSourceLineNotFoundError(
                    "Requested source line does not "
                    "belong to the sales order: "
                    f"line_id="
                    f"{request.trade_document_line_id}"
                )
            )

        if line.warehouse_id is None:
            raise (
                SalesOrderFulfillmentWarehouseRequiredError(
                    "Source line has no reservation "
                    "warehouse: "
                    f"line_id={line.id}"
                )
            )

        source_quantity = Decimal(
            line.quantity
        )

        fulfilled_before = (
            current_fulfilled[
                line.id
            ]
        )

        reserved_before = (
            current_reserved[
                line.id
            ]
        )

        fulfilled_after = (
            fulfilled_before
            + request.quantity
        )

        if fulfilled_after > source_quantity:
            raise SalesOrderOverFulfillmentError(
                "Fulfillment exceeds remaining "
                "source quantity: "
                f"line_id={line.id}, "
                f"requested={request.quantity}, "
                f"fulfilled_before="
                f"{fulfilled_before}, "
                f"source={source_quantity}"
            )

        if request.quantity > reserved_before:
            raise (
                SalesOrderInsufficientReservationError(
                    "Fulfillment exceeds outstanding "
                    "reservation: "
                    f"line_id={line.id}, "
                    f"requested={request.quantity}, "
                    f"reserved={reserved_before}"
                )
            )

        reserved_after = (
            reserved_before
            - request.quantity
        )

        resulting_fulfilled[
            line.id
        ] = fulfilled_after

        plan_lines.append(
            SalesOrderFulfillmentPlanLine(
                source_line=line,
                quantity=request.quantity,
                fulfilled_before=(
                    fulfilled_before
                ),
                fulfilled_after=(
                    fulfilled_after
                ),
                reserved_before=(
                    reserved_before
                ),
                reserved_after=(
                    reserved_after
                ),
            )
        )

    resulting_status = (
        calculate_sales_order_fulfillment_status(
            document,
            resulting_fulfilled,
        )
    )

    ordered_plan_lines = tuple(
        sorted(
            plan_lines,
            key=lambda item: (
                item.source_line.product_id,
                item.source_line.warehouse_id,
                item.source_line.id,
            ),
        )
    )

    return SalesOrderFulfillmentPlan(
        lines=ordered_plan_lines,
        resulting_status=resulting_status,
    )


async def get_persisted_fulfilled_quantities(
    db: AsyncSession,
    *,
    company_id: int,
    trade_document_id: int,
    source_line_ids: Sequence[int],
) -> dict[int, Decimal]:
    """
    Return posted fulfillment quantity by Sales Order line.

    Only POSTED warehouse documents count as fulfilled.
    """

    line_ids = tuple(
        sorted(
            set(
                source_line_ids
            )
        )
    )

    if not line_ids:
        return {}

    result = await db.execute(
        select(
            TradeFulfillmentLine
            .trade_document_line_id,
            func.sum(
                TradeFulfillmentLine.quantity
            ),
        )
        .join(
            Document,
            and_(
                Document.id
                == TradeFulfillmentLine
                .warehouse_document_id,
                Document.company_id
                == TradeFulfillmentLine
                .company_id,
            ),
        )
        .where(
            TradeFulfillmentLine.company_id
            == company_id,
            TradeFulfillmentLine.trade_document_id
            == trade_document_id,
            TradeFulfillmentLine.trade_document_line_id
            .in_(line_ids),
            Document.status
            == DocumentStatus.POSTED,
        )
        .group_by(
            TradeFulfillmentLine
            .trade_document_line_id
        )
    )

    quantities = {
        line_id: ZERO
        for line_id in line_ids
    }

    for (
        line_id,
        quantity,
    ) in result.all():
        quantities[
            line_id
        ] = Decimal(quantity)

    return quantities


async def get_outstanding_reservation_quantities(
    db: AsyncSession,
    *,
    company_id: int,
    trade_document_id: int,
    source_line_ids: Sequence[int],
) -> dict[int, Decimal]:
    """
    Load outstanding persistent reservation quantity for each
    Sales Order source line.
    """

    quantities: dict[
        int,
        Decimal,
    ] = {}

    for line_id in sorted(
        set(
            source_line_ids
        )
    ):
        quantities[line_id] = (
            await get_reserved_quantity_for_source_line(
                db,
                company_id=company_id,
                source_document_id=(
                    trade_document_id
                ),
                source_document_line_id=(
                    line_id
                ),
            )
        )

    return quantities


async def build_sales_order_fulfillment_plan(
    db: AsyncSession,
    *,
    document: TradeDocument,
    request_lines: Sequence[
        SalesOrderFulfillmentRequestLine
    ],
) -> SalesOrderFulfillmentPlan:
    """
    Load persistent fulfillment/reservation state and build
    the pure plan.

    Caller is responsible for locking the TradeDocument header
    before calling this function in the write path.
    """

    validate_sales_order_fulfillment_state(
        document
    )

    source_line_ids = [
        line.id
        for line in document.lines
        if line.id is not None
    ]

    fulfilled_quantities = (
        await get_persisted_fulfilled_quantities(
            db,
            company_id=document.company_id,
            trade_document_id=document.id,
            source_line_ids=source_line_ids,
        )
    )

    reserved_quantities = (
        await get_outstanding_reservation_quantities(
            db,
            company_id=document.company_id,
            trade_document_id=document.id,
            source_line_ids=source_line_ids,
        )
    )

    return create_sales_order_fulfillment_plan(
        document,
        request_lines,
        fulfilled_quantities=(
            fulfilled_quantities
        ),
        reserved_quantities=(
            reserved_quantities
        ),
    )



@dataclass(
    frozen=True,
    slots=True,
)
class SalesOrderFulfillmentExecutionResult:
    """
    Result of one atomic Sales Order fulfillment.

    Caller still owns COMMIT / ROLLBACK.
    """

    sales_order: TradeDocument
    warehouse_document: Document
    fulfillment: TradeFulfillment
    journal_entry: JournalEntry


class SalesOrderFulfillmentDocumentNumberError(
    SalesOrderFulfillmentError
):
    """Warehouse ISSUE document number is invalid."""


class SalesOrderFulfillmentExecutionStateError(
    SalesOrderFulfillmentError
):
    """Atomic execution produced an invalid internal state."""


async def execute_sales_order_fulfillment(
    db: AsyncSession,
    *,
    company_id: int,
    trade_document_id: int,
    warehouse_document_number: str,
    document_date: date,
    accounting_rule_id: int,
    created_by: int,
    request_lines: Sequence[
        SalesOrderFulfillmentRequestLine
    ],
) -> SalesOrderFulfillmentExecutionResult:
    """
    Execute one Sales Order fulfillment atomically.

    Caller owns COMMIT / ROLLBACK.

    Sequence:

        1. lock Sales Order header
        2. build persistent fulfillment plan
        3. create DRAFT warehouse ISSUE + lines
        4. create persistent fulfillment mappings
        5. CONSUME reservations in global stock-lock order
        6. post warehouse ISSUE
        7. update Sales Order lifecycle status
        8. flush

    No commit occurs in this service.

    Any failure must be rolled back by the caller, which removes
    the ISSUE, mappings, reservation movements, stock changes,
    costing, accounting and Sales Order status change together.
    """

    number = warehouse_document_number.strip()

    if not number:
        raise SalesOrderFulfillmentDocumentNumberError(
            "Warehouse ISSUE document number is required"
        )

    # ---------------------------------------------------------
    # 1. LOCK SALES ORDER HEADER
    # ---------------------------------------------------------

    sales_order = await get_locked_trade_document(
        db,
        company_id=company_id,
        document_id=trade_document_id,
    )

    # ---------------------------------------------------------
    # 2. BUILD PLAN FROM PERSISTENT STATE
    # ---------------------------------------------------------

    plan = await build_sales_order_fulfillment_plan(
        db,
        document=sales_order,
        request_lines=request_lines,
    )

    if not plan.lines:
        raise SalesOrderFulfillmentExecutionStateError(
            "Fulfillment plan contains no lines"
        )

    # ---------------------------------------------------------
    # 3. CREATE DRAFT WAREHOUSE ISSUE
    # ---------------------------------------------------------

    warehouse_document = Document(
        company_id=company_id,
        accounting_rule_id=accounting_rule_id,
        number=number,
        document_type=DocumentType.ISSUE,
        document_date=document_date,
        status=DocumentStatus.DRAFT,
        created_by=created_by,
    )

    db.add(
        warehouse_document
    )

    # Need the warehouse document PK before its lines
    # and persistent fulfillment header are created.
    await db.flush()

    if warehouse_document.id is None:
        raise SalesOrderFulfillmentExecutionStateError(
            "Warehouse ISSUE did not receive an ID"
        )

    target_lines_by_source_id: dict[
        int,
        DocumentLine,
    ] = {}

    # plan.lines is already in the global deterministic
    # product -> warehouse -> source-line lock order.
    for plan_line in plan.lines:
        source_line = plan_line.source_line

        if source_line.id is None:
            raise SalesOrderFulfillmentExecutionStateError(
                "Fulfillment source line has no ID"
            )

        if source_line.warehouse_id is None:
            raise SalesOrderFulfillmentExecutionStateError(
                "Fulfillment source line has no warehouse"
            )

        warehouse_line = DocumentLine(
            document_id=warehouse_document.id,
            product_id=source_line.product_id,
            warehouse_id=source_line.warehouse_id,
            quantity=plan_line.quantity,
            price=Decimal(
                source_line.unit_price
            ),
        )

        db.add(
            warehouse_line
        )

        target_lines_by_source_id[
            source_line.id
        ] = warehouse_line

    # Target DocumentLine IDs are required by the
    # fulfillment mapping FK.
    await db.flush()

    for (
        source_line_id,
        warehouse_line,
    ) in target_lines_by_source_id.items():
        if warehouse_line.id is None:
            raise SalesOrderFulfillmentExecutionStateError(
                "Warehouse ISSUE line did not receive an ID: "
                f"source_line_id={source_line_id}"
            )

    # ---------------------------------------------------------
    # 4. CREATE PERSISTENT FULFILLMENT HEADER
    # ---------------------------------------------------------

    fulfillment = TradeFulfillment(
        company_id=company_id,
        trade_document_id=sales_order.id,
        warehouse_document_id=(
            warehouse_document.id
        ),
        warehouse_document_type=(
            DocumentType.ISSUE.value
        ),
        created_by=created_by,
    )

    db.add(
        fulfillment
    )

    await db.flush()

    if fulfillment.id is None:
        raise SalesOrderFulfillmentExecutionStateError(
            "Trade fulfillment did not receive an ID"
        )

    # ---------------------------------------------------------
    # 5. CREATE SOURCE -> TARGET LINE MAPPINGS
    # ---------------------------------------------------------

    for plan_line in plan.lines:
        source_line = plan_line.source_line

        warehouse_line = (
            target_lines_by_source_id[
                source_line.id
            ]
        )

        mapping = TradeFulfillmentLine(
            company_id=company_id,
            fulfillment_id=fulfillment.id,
            trade_document_id=sales_order.id,
            trade_document_line_id=source_line.id,
            warehouse_document_id=(
                warehouse_document.id
            ),
            warehouse_document_line_id=(
                warehouse_line.id
            ),
            product_id=source_line.product_id,
            warehouse_id=source_line.warehouse_id,
            quantity=plan_line.quantity,
        )

        db.add(
            mapping
        )

    # Make mappings physically present before reservation
    # consumption and posting.
    await db.flush()

    # ---------------------------------------------------------
    # 6. CONSUME OUTSTANDING RESERVATIONS
    # ---------------------------------------------------------

    # This uses the same deterministic order as warehouse posting:
    #
    #     product_id -> warehouse_id -> source line id
    #
    # Reservation persistence locks:
    #
    #     TradeDocumentLine -> StockBalance
    #
    # The StockBalance lock is then retained until transaction end.
    for plan_line in plan.lines:
        source_line = plan_line.source_line

        await consume_source_line_reservation(
            db,
            company_id=company_id,
            source_document_id=sales_order.id,
            source_document_line_id=source_line.id,
            quantity=plan_line.quantity,
        )

    # ---------------------------------------------------------
    # 7. POST WAREHOUSE ISSUE
    # ---------------------------------------------------------

    posted_document, journal_entry = (
        await post_document(
            db=db,
            company_id=company_id,
            document_id=warehouse_document.id,
            accounting_rule_id=accounting_rule_id,
            created_by=created_by,
        )
    )

    if (
        posted_document.id
        != warehouse_document.id
    ):
        raise SalesOrderFulfillmentExecutionStateError(
            "Posting returned a different "
            "warehouse document"
        )

    if (
        posted_document.status
        != DocumentStatus.POSTED
    ):
        raise SalesOrderFulfillmentExecutionStateError(
            "Warehouse ISSUE was not posted"
        )

    # ---------------------------------------------------------
    # 8. UPDATE SALES ORDER STATUS
    # ---------------------------------------------------------

    sales_order.status = (
        plan.resulting_status
    )

    await db.flush()

    return SalesOrderFulfillmentExecutionResult(
        sales_order=sales_order,
        warehouse_document=posted_document,
        fulfillment=fulfillment,
        journal_entry=journal_entry,
    )
