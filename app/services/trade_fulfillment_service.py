from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.services.invoice_fulfillment_allocation_service import (
    has_active_fulfillment_allocations,
)
from app.services.accounting_account_role_resolver import (
    AccountingAccountRoleResolutionError,
    resolve_company_account_roles,
)
from app.services.accounting_account_roles import (
    AccountingAccountRole,
)

from app.models.accounting_rule import AccountingRule
from app.services.sales_fulfillment_accounting_rule import (
    SalesFulfillmentAccountingRuleValidationError,
    validate_sales_fulfillment_accounting_rule,
)
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
    get_locked_source_line,
    get_reserved_quantity_for_source_line,
    reserve_source_line,
)
from app.services.document_posting import (
    post_document,
)
from app.services.document_reversal import (
    DocumentReversalError,
    reverse_document_for_trade_fulfillment,
)
from app.services.trade_document_lifecycle_service import (
    get_locked_purchase_order,
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


class SalesOrderFulfillmentAccountingRuleError(
    SalesOrderFulfillmentError
):
    """Accounting rule is unsafe for Sales Order fulfillment."""


async def validate_sales_fulfillment_accounting_rule_for_company(
    db: AsyncSession,
    *,
    company_id: int,
    accounting_rule_id: int,
) -> AccountingRule:
    """
    Validate the accounting rule selected for Sales Order
    warehouse fulfillment.

    The warehouse ISSUE may recognize only COGS and inventory
    reduction. Commercial receivables, revenue and VAT are
    handled by their separate business lifecycles.
    """

    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if accounting_rule_id <= 0:
        raise ValueError(
            "accounting_rule_id must be greater than zero"
        )

    accounting_rule = (
        await db.execute(
            select(AccountingRule)
            .options(
                selectinload(
                    AccountingRule.lines
                )
            )
            .where(
                AccountingRule.id
                == accounting_rule_id,
                AccountingRule.company_id
                == company_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if accounting_rule is None:
        raise SalesOrderFulfillmentAccountingRuleError(
            "Sales fulfillment accounting rule not found"
        )

    try:
        accounts = await resolve_company_account_roles(
            db,
            company_id=company_id,
            roles=(
                AccountingAccountRole.GOODS_COGS,
                AccountingAccountRole.INVENTORY_GOODS,
            ),
        )
    except AccountingAccountRoleResolutionError as exc:
        raise SalesOrderFulfillmentAccountingRuleError(
            "Sales fulfillment accounting roles "
            f"cannot be resolved: {exc}"
        ) from exc

    try:
        validate_sales_fulfillment_accounting_rule(
            accounting_rule=accounting_rule,
            cogs_account_id=(
                accounts[
                    AccountingAccountRole.GOODS_COGS
                ].id
            ),
            inventory_account_id=(
                accounts[
                    AccountingAccountRole.INVENTORY_GOODS
                ].id
            ),
        )
    except (
        SalesFulfillmentAccountingRuleValidationError
    ) as exc:
        raise SalesOrderFulfillmentAccountingRuleError(
            str(exc)
        ) from exc

    return accounting_rule



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
    warehouse_document_type: DocumentType,
) -> dict[int, Decimal]:
    """
    Return posted fulfillment quantity by TradeDocument line.

    Only POSTED warehouse documents of the explicitly requested
    fulfillment target type count as fulfilled.
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
            Document.document_type
            == warehouse_document_type,
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
            warehouse_document_type=(
                DocumentType.ISSUE
            ),
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




# =============================================================
# PURCHASE ORDER FULFILLMENT
# =============================================================


class PurchaseOrderFulfillmentError(Exception):
    """Base error for Purchase Order fulfillment."""


class PurchaseOrderFulfillmentTypeError(
    PurchaseOrderFulfillmentError
):
    """Trade document is not a Purchase Order."""


class PurchaseOrderFulfillmentStatusError(
    PurchaseOrderFulfillmentError
):
    """Purchase Order cannot be fulfilled in current state."""


class PurchaseOrderFulfillmentLinesRequiredError(
    PurchaseOrderFulfillmentError
):
    """Purchase Order has no source lines."""


class PurchaseOrderFulfillmentRequestRequiredError(
    PurchaseOrderFulfillmentError
):
    """Receipt fulfillment request has no lines."""


class PurchaseOrderFulfillmentDuplicateLineError(
    PurchaseOrderFulfillmentError
):
    """The same source line appears more than once."""


class PurchaseOrderFulfillmentSourceLineNotFoundError(
    PurchaseOrderFulfillmentError
):
    """Requested line does not belong to Purchase Order."""


class PurchaseOrderFulfillmentWarehouseRequiredError(
    PurchaseOrderFulfillmentError
):
    """Purchase Order line has no receipt warehouse."""


class PurchaseOrderFulfillmentStateError(
    PurchaseOrderFulfillmentError
):
    """Stored Purchase Order fulfillment state is invalid."""


class PurchaseOrderOverFulfillmentError(
    PurchaseOrderFulfillmentError
):
    """Requested receipt exceeds remaining order quantity."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseOrderFulfillmentRequestLine:
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
class PurchaseOrderFulfillmentPlanLine:
    source_line: TradeDocumentLine
    quantity: Decimal
    fulfilled_before: Decimal
    fulfilled_after: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseOrderFulfillmentPlan:
    lines: tuple[
        PurchaseOrderFulfillmentPlanLine,
        ...,
    ]

    resulting_status: TradeDocumentStatus


def validate_purchase_order_fulfillment_state(
    document: TradeDocument,
) -> None:
    """
    Purchase receipt fulfillment is allowed only for an
    already confirmed Purchase Order.

    PARTIALLY_FULFILLED may be fulfilled again.

    DRAFT, FULFILLED and CANCELLED are rejected.
    """
    if (
        document.direction
        != TradeDirection.PURCHASE
    ):
        raise PurchaseOrderFulfillmentTypeError(
            "Only purchase trade documents can be "
            "fulfilled as purchase orders"
        )

    if (
        document.kind
        != TradeDocumentKind.ORDER
    ):
        raise PurchaseOrderFulfillmentTypeError(
            "Only trade document kind 'order' can be "
            "fulfilled as a purchase order"
        )

    if document.status not in (
        TradeDocumentStatus.CONFIRMED,
        TradeDocumentStatus.PARTIALLY_FULFILLED,
    ):
        raise PurchaseOrderFulfillmentStatusError(
            "Only confirmed or partially fulfilled "
            "purchase orders can be fulfilled"
        )

    if not document.lines:
        raise PurchaseOrderFulfillmentLinesRequiredError(
            "Purchase order must contain at least one line"
        )

    missing_warehouse_lines = [
        line.line_number
        for line in document.lines
        if line.warehouse_id is None
    ]

    if missing_warehouse_lines:
        raise PurchaseOrderFulfillmentWarehouseRequiredError(
            "Purchase order has source lines without "
            "a receipt warehouse: "
            + ", ".join(
                str(line_number)
                for line_number
                in sorted(
                    missing_warehouse_lines
                )
            )
        )


def calculate_purchase_order_fulfillment_status(
    document: TradeDocument,
    fulfilled_quantities: Mapping[
        int,
        Decimal,
    ],
) -> TradeDocumentStatus:
    """
    Calculate Purchase Order lifecycle status from persistent
    fulfilled quantities.

    Fulfilled means quantity represented by POSTED warehouse
    RECEIPT fulfillment targets.
    """
    if not document.lines:
        raise PurchaseOrderFulfillmentLinesRequiredError(
            "Purchase order must contain at least one line"
        )

    any_fulfilled = False
    all_fulfilled = True

    for line in document.lines:
        if (
            line.id is None
            or line.id <= 0
        ):
            raise PurchaseOrderFulfillmentStateError(
                "Purchase order contains an unpersisted "
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
            raise PurchaseOrderFulfillmentStateError(
                "Fulfilled quantity cannot be negative: "
                f"line_id={line.id}, "
                f"fulfilled={fulfilled_quantity}"
            )

        if (
            fulfilled_quantity
            > source_quantity
        ):
            raise PurchaseOrderFulfillmentStateError(
                "Persisted fulfillment exceeds "
                "source quantity: "
                f"line_id={line.id}, "
                f"source={source_quantity}, "
                f"fulfilled={fulfilled_quantity}"
            )

        if fulfilled_quantity > ZERO:
            any_fulfilled = True

        if (
            fulfilled_quantity
            != source_quantity
        ):
            all_fulfilled = False

    if all_fulfilled:
        return TradeDocumentStatus.FULFILLED

    if any_fulfilled:
        return (
            TradeDocumentStatus
            .PARTIALLY_FULFILLED
        )

    return TradeDocumentStatus.CONFIRMED


def create_purchase_order_fulfillment_plan(
    document: TradeDocument,
    request_lines: Sequence[
        PurchaseOrderFulfillmentRequestLine
    ],
    *,
    fulfilled_quantities: Mapping[
        int,
        Decimal,
    ],
) -> PurchaseOrderFulfillmentPlan:
    """
    Pure Purchase Order receipt planner.

    No database writes.
    No reservation state exists for Purchase Orders.

    Invariant:

        0 <= posted fulfilled quantity <= source quantity
    """
    validate_purchase_order_fulfillment_state(
        document
    )

    requests = tuple(
        request_lines
    )

    if not requests:
        raise (
            PurchaseOrderFulfillmentRequestRequiredError(
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
            PurchaseOrderFulfillmentDuplicateLineError(
                "Each purchase-order source line may "
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

    for line in document.lines:
        if (
            line.id is None
            or line.id <= 0
        ):
            raise PurchaseOrderFulfillmentStateError(
                "Purchase order contains an unpersisted "
                "source line"
            )

        source_lines[
            line.id
        ] = line

        source_quantity = Decimal(
            line.quantity
        )

        fulfilled_before = Decimal(
            fulfilled_quantities.get(
                line.id,
                ZERO,
            )
        )

        if fulfilled_before < ZERO:
            raise PurchaseOrderFulfillmentStateError(
                "Fulfilled quantity cannot be negative: "
                f"line_id={line.id}"
            )

        if (
            fulfilled_before
            > source_quantity
        ):
            raise PurchaseOrderFulfillmentStateError(
                "Persisted fulfillment exceeds "
                "source quantity: "
                f"line_id={line.id}"
            )

        current_fulfilled[
            line.id
        ] = fulfilled_before

    plan_lines: list[
        PurchaseOrderFulfillmentPlanLine
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
                PurchaseOrderFulfillmentSourceLineNotFoundError(
                    "Requested source line does not "
                    "belong to the purchase order: "
                    f"line_id="
                    f"{request.trade_document_line_id}"
                )
            )

        if line.warehouse_id is None:
            raise (
                PurchaseOrderFulfillmentWarehouseRequiredError(
                    "Source line has no receipt warehouse: "
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

        fulfilled_after = (
            fulfilled_before
            + request.quantity
        )

        if (
            fulfilled_after
            > source_quantity
        ):
            raise PurchaseOrderOverFulfillmentError(
                "Fulfillment exceeds remaining "
                "source quantity: "
                f"line_id={line.id}, "
                f"requested={request.quantity}, "
                f"fulfilled_before="
                f"{fulfilled_before}, "
                f"source={source_quantity}"
            )

        resulting_fulfilled[
            line.id
        ] = fulfilled_after

        plan_lines.append(
            PurchaseOrderFulfillmentPlanLine(
                source_line=line,
                quantity=request.quantity,
                fulfilled_before=(
                    fulfilled_before
                ),
                fulfilled_after=(
                    fulfilled_after
                ),
            )
        )

    resulting_status = (
        calculate_purchase_order_fulfillment_status(
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

    return PurchaseOrderFulfillmentPlan(
        lines=ordered_plan_lines,
        resulting_status=resulting_status,
    )


async def build_purchase_order_fulfillment_plan(
    db: AsyncSession,
    *,
    document: TradeDocument,
    request_lines: Sequence[
        PurchaseOrderFulfillmentRequestLine
    ],
) -> PurchaseOrderFulfillmentPlan:
    """
    Load persistent POSTED fulfillment state and build the
    Purchase Order receipt plan.

    Caller owns the TradeDocument header lock.
    """
    validate_purchase_order_fulfillment_state(
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
            warehouse_document_type=(
                DocumentType.RECEIPT
            ),
        )
    )

    return create_purchase_order_fulfillment_plan(
        document,
        request_lines,
        fulfilled_quantities=(
            fulfilled_quantities
        ),
    )


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseOrderFulfillmentExecutionResult:
    """
    Result of one atomic Purchase Order receipt fulfillment.

    Caller still owns COMMIT / ROLLBACK.
    """

    purchase_order: TradeDocument
    warehouse_document: Document
    fulfillment: TradeFulfillment
    journal_entry: JournalEntry


class PurchaseOrderFulfillmentDocumentNumberError(
    PurchaseOrderFulfillmentError
):
    """Warehouse RECEIPT document number is invalid."""


class PurchaseOrderFulfillmentExecutionStateError(
    PurchaseOrderFulfillmentError
):
    """Atomic Purchase fulfillment produced invalid state."""


async def execute_purchase_order_fulfillment(
    db: AsyncSession,
    *,
    company_id: int,
    trade_document_id: int,
    warehouse_document_number: str,
    document_date: date,
    accounting_rule_id: int,
    created_by: int,
    request_lines: Sequence[
        PurchaseOrderFulfillmentRequestLine
    ],
) -> PurchaseOrderFulfillmentExecutionResult:
    """
    Execute one Purchase Order receipt fulfillment atomically.

    Caller owns COMMIT / ROLLBACK.

    Sequence:

        1. lock Purchase Order header
        2. build persistent receipt plan
        3. create DRAFT warehouse RECEIPT + lines
        4. create persistent TradeFulfillment header
        5. create source -> target line mappings
        6. post warehouse RECEIPT
        7. update Purchase Order lifecycle status
        8. flush

    Purchase Order fulfillment deliberately performs no stock
    reservation operations.

    Any failure must be rolled back by the caller, which removes
    the RECEIPT, mappings, stock/cost/accounting changes and the
    Purchase Order status change together.
    """
    number = warehouse_document_number.strip()

    if not number:
        raise PurchaseOrderFulfillmentDocumentNumberError(
            "Warehouse RECEIPT document number is required"
        )

    purchase_order = await get_locked_purchase_order(
        db,
        company_id=company_id,
        document_id=trade_document_id,
    )

    plan = await build_purchase_order_fulfillment_plan(
        db,
        document=purchase_order,
        request_lines=request_lines,
    )

    if not plan.lines:
        raise PurchaseOrderFulfillmentExecutionStateError(
            "Fulfillment plan contains no lines"
        )

    warehouse_document = Document(
        company_id=company_id,
        accounting_rule_id=accounting_rule_id,
        number=number,
        document_type=DocumentType.RECEIPT,
        document_date=document_date,
        status=DocumentStatus.DRAFT,
        created_by=created_by,
    )

    db.add(
        warehouse_document
    )

    await db.flush()

    if warehouse_document.id is None:
        raise PurchaseOrderFulfillmentExecutionStateError(
            "Warehouse RECEIPT did not receive an ID"
        )

    target_lines_by_source_id: dict[
        int,
        DocumentLine,
    ] = {}

    for plan_line in plan.lines:
        source_line = plan_line.source_line

        if source_line.id is None:
            raise PurchaseOrderFulfillmentExecutionStateError(
                "Fulfillment source line has no ID"
            )

        if source_line.warehouse_id is None:
            raise PurchaseOrderFulfillmentExecutionStateError(
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

    await db.flush()

    for (
        source_line_id,
        warehouse_line,
    ) in target_lines_by_source_id.items():
        if warehouse_line.id is None:
            raise PurchaseOrderFulfillmentExecutionStateError(
                "Warehouse RECEIPT line did not receive an ID: "
                f"source_line_id={source_line_id}"
            )

    fulfillment = TradeFulfillment(
        company_id=company_id,
        trade_document_id=purchase_order.id,
        warehouse_document_id=(
            warehouse_document.id
        ),
        warehouse_document_type=(
            DocumentType.RECEIPT.value
        ),
        created_by=created_by,
    )

    db.add(
        fulfillment
    )

    await db.flush()

    if fulfillment.id is None:
        raise PurchaseOrderFulfillmentExecutionStateError(
            "Trade fulfillment did not receive an ID"
        )

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
            trade_document_id=purchase_order.id,
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

    await db.flush()

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
        raise PurchaseOrderFulfillmentExecutionStateError(
            "Posting returned a different "
            "warehouse document"
        )

    if (
        posted_document.status
        != DocumentStatus.POSTED
    ):
        raise PurchaseOrderFulfillmentExecutionStateError(
            "Warehouse RECEIPT was not posted"
        )

    purchase_order.status = (
        plan.resulting_status
    )

    await db.flush()

    return PurchaseOrderFulfillmentExecutionResult(
        purchase_order=purchase_order,
        warehouse_document=posted_document,
        fulfillment=fulfillment,
        journal_entry=journal_entry,
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
    # VALIDATE INVENTORY-ONLY ACCOUNTING RULE
    # ---------------------------------------------------------

    await validate_sales_fulfillment_accounting_rule_for_company(
        db,
        company_id=company_id,
        accounting_rule_id=accounting_rule_id,
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


class SalesOrderFulfillmentReversalNotFoundError(
    SalesOrderFulfillmentError
):
    """Requested persistent fulfillment does not exist."""


class SalesOrderFulfillmentReversalStatusError(
    SalesOrderFulfillmentError
):
    """Sales Order cannot reverse fulfillment in this state."""


class SalesOrderFulfillmentReversalStateError(
    SalesOrderFulfillmentError
):
    """Persistent fulfillment reversal state is invalid."""


@dataclass(
    frozen=True,
    slots=True,
)
class SalesOrderFulfillmentReversalExecutionResult:
    """
    Result of one atomic Sales Order fulfillment reversal.

    Caller owns COMMIT / ROLLBACK.
    """

    sales_order: TradeDocument
    warehouse_document: Document
    fulfillment: TradeFulfillment


def validate_sales_order_fulfillment_reversal_state(
    document: TradeDocument,
) -> None:
    """
    A fulfillment can only be reversed from an order that
    currently has active posted fulfillment.
    """

    if document.direction != TradeDirection.SALE:
        raise SalesOrderFulfillmentTypeError(
            "Only sale trade documents can reverse "
            "sales-order fulfillment"
        )

    if document.kind != TradeDocumentKind.ORDER:
        raise SalesOrderFulfillmentTypeError(
            "Only trade document kind 'order' can reverse "
            "sales-order fulfillment"
        )

    if document.status not in (
        TradeDocumentStatus.PARTIALLY_FULFILLED,
        TradeDocumentStatus.FULFILLED,
    ):
        raise SalesOrderFulfillmentReversalStatusError(
            "Only partially fulfilled or fulfilled "
            "sales orders can reverse fulfillment"
        )

    if not document.lines:
        raise SalesOrderFulfillmentLinesRequiredError(
            "Sales order must contain at least one line"
        )


async def get_locked_trade_fulfillment(
    db: AsyncSession,
    *,
    company_id: int,
    trade_document_id: int,
    fulfillment_id: int,
) -> TradeFulfillment:
    """
    Lock one exact fulfillment header belonging to the
    requested Sales Order and company.
    """

    result = await db.execute(
        select(
            TradeFulfillment
        )
        .where(
            TradeFulfillment.id
            == fulfillment_id,
            TradeFulfillment.company_id
            == company_id,
            TradeFulfillment.trade_document_id
            == trade_document_id,
        )
        .with_for_update()
    )

    fulfillment = (
        result.scalar_one_or_none()
    )

    if fulfillment is None:
        raise (
            SalesOrderFulfillmentReversalNotFoundError(
                "Trade fulfillment not found"
            )
        )

    return fulfillment


async def get_trade_fulfillment_lines_for_reversal(
    db: AsyncSession,
    *,
    company_id: int,
    trade_document_id: int,
    fulfillment_id: int,
) -> tuple[TradeFulfillmentLine, ...]:
    """
    Lock persistent fulfillment mappings in deterministic
    source stock-key order.
    """

    result = await db.execute(
        select(
            TradeFulfillmentLine
        )
        .where(
            TradeFulfillmentLine.company_id
            == company_id,
            TradeFulfillmentLine.trade_document_id
            == trade_document_id,
            TradeFulfillmentLine.fulfillment_id
            == fulfillment_id,
        )
        .order_by(
            TradeFulfillmentLine.product_id,
            TradeFulfillmentLine.warehouse_id,
            TradeFulfillmentLine.trade_document_line_id,
            TradeFulfillmentLine.id,
        )
        .with_for_update()
    )

    return tuple(
        result.scalars().all()
    )


def validate_sales_order_reversal_balances(
    document: TradeDocument,
    *,
    fulfilled_quantities: Mapping[
        int,
        Decimal,
    ],
    reserved_quantities: Mapping[
        int,
        Decimal,
    ],
) -> None:
    """
    Sales Order invariant:

        active posted fulfillment
        + outstanding reservation
        == source quantity

    The invariant must hold before and after reversal.
    """

    for line in document.lines:
        if line.id is None or line.id <= 0:
            raise SalesOrderFulfillmentReversalStateError(
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

        reserved_quantity = Decimal(
            reserved_quantities.get(
                line.id,
                ZERO,
            )
        )

        if fulfilled_quantity < ZERO:
            raise SalesOrderFulfillmentReversalStateError(
                "Fulfilled quantity cannot be negative"
            )

        if reserved_quantity < ZERO:
            raise SalesOrderFulfillmentReversalStateError(
                "Reserved quantity cannot be negative"
            )

        if fulfilled_quantity > source_quantity:
            raise SalesOrderFulfillmentReversalStateError(
                "Persisted fulfillment exceeds "
                "source quantity"
            )

        if reserved_quantity > source_quantity:
            raise SalesOrderFulfillmentReversalStateError(
                "Reservation exceeds source quantity"
            )

        if (
            fulfilled_quantity
            + reserved_quantity
            != source_quantity
        ):
            raise SalesOrderFulfillmentReversalStateError(
                "Sales order fulfillment/reservation "
                "balance is inconsistent: "
                f"line_id={line.id}, "
                f"source={source_quantity}, "
                f"fulfilled={fulfilled_quantity}, "
                f"reserved={reserved_quantity}"
            )


async def execute_sales_order_fulfillment_reversal(
    db: AsyncSession,
    *,
    company_id: int,
    trade_document_id: int,
    fulfillment_id: int,
    reversal_date: date,
    reversed_by: int,
) -> SalesOrderFulfillmentReversalExecutionResult:
    """
    Reverse one persistent Sales Order fulfillment atomically.

    Sequence:

        1. lock Sales Order header
        2. validate Sales Order lifecycle
        3. lock fulfillment header + mappings
        4. validate persistent fulfillment state
        5. lock source TradeDocumentLines deterministically
        6. validate fulfillment/reservation invariant
        7. reverse linked warehouse ISSUE
        8. restore reservation with RESERVE movements
        9. recalculate active POSTED fulfillment
       10. recalculate Sales Order lifecycle status
       11. validate restored invariant
       12. flush

    No commit occurs here.
    """

    if fulfillment_id <= 0:
        raise ValueError(
            "Fulfillment ID must be greater than zero"
        )

    # ---------------------------------------------------------
    # 1. LOCK SALES ORDER
    # ---------------------------------------------------------

    sales_order = await get_locked_trade_document(
        db,
        company_id=company_id,
        document_id=trade_document_id,
    )

    validate_sales_order_fulfillment_reversal_state(
        sales_order
    )

    # ---------------------------------------------------------
    # 2. LOCK FULFILLMENT
    # ---------------------------------------------------------

    fulfillment = await get_locked_trade_fulfillment(
        db,
        company_id=company_id,
        trade_document_id=sales_order.id,
        fulfillment_id=fulfillment_id,
    )

    if await has_active_fulfillment_allocations(
        db,
        company_id=company_id,
        fulfillment_id=fulfillment.id,
        lock_rows=True,
    ):
        raise SalesOrderFulfillmentReversalStateError(
            "Trade fulfillment has ACTIVE Invoice "
            "allocations; reverse matching allocations "
            "before fulfillment reversal"
        )

    mappings = (
        await get_trade_fulfillment_lines_for_reversal(
            db,
            company_id=company_id,
            trade_document_id=sales_order.id,
            fulfillment_id=fulfillment.id,
        )
    )

    if not mappings:
        raise SalesOrderFulfillmentReversalStateError(
            "Trade fulfillment has no line mappings"
        )

    # ---------------------------------------------------------
    # 3. VALIDATE / AGGREGATE MAPPINGS
    # ---------------------------------------------------------

    source_lines_by_id = {
        line.id: line
        for line in sales_order.lines
        if line.id is not None
    }

    quantities_to_restore: dict[
        int,
        Decimal,
    ] = {}

    for mapping in mappings:
        if (
            mapping.warehouse_document_id
            != fulfillment.warehouse_document_id
        ):
            raise SalesOrderFulfillmentReversalStateError(
                "Fulfillment line points to a different "
                "warehouse document"
            )

        source_line = source_lines_by_id.get(
            mapping.trade_document_line_id
        )

        if source_line is None:
            raise SalesOrderFulfillmentReversalStateError(
                "Fulfillment mapping references a source "
                "line outside the Sales Order"
            )

        if (
            source_line.product_id
            != mapping.product_id
        ):
            raise SalesOrderFulfillmentReversalStateError(
                "Fulfillment mapping product does not "
                "match the source line"
            )

        if (
            source_line.warehouse_id
            != mapping.warehouse_id
        ):
            raise SalesOrderFulfillmentReversalStateError(
                "Fulfillment mapping warehouse does not "
                "match the source line"
            )

        quantity = Decimal(
            mapping.quantity
        )

        if quantity <= ZERO:
            raise SalesOrderFulfillmentReversalStateError(
                "Fulfillment mapping quantity must be "
                "greater than zero"
            )

        quantities_to_restore[
            source_line.id
        ] = (
            quantities_to_restore.get(
                source_line.id,
                ZERO,
            )
            + quantity
        )

    ordered_source_lines = tuple(
        sorted(
            (
                source_lines_by_id[line_id]
                for line_id
                in quantities_to_restore
            ),
            key=lambda line: (
                line.product_id,
                line.warehouse_id,
                line.id,
            ),
        )
    )

    # ---------------------------------------------------------
    # 4. PRELOCK SOURCE LINES
    # ---------------------------------------------------------
    #
    # Reservation operations lock:
    #
    #     TradeDocumentLine -> StockBalance
    #
    # Prelocking source lines before document reversal avoids
    # introducing the opposite lock order.
    # ---------------------------------------------------------

    for source_line in ordered_source_lines:
        locked_line = await get_locked_source_line(
            db,
            company_id=company_id,
            source_document_id=sales_order.id,
            source_document_line_id=source_line.id,
        )

        if (
            locked_line.product_id
            != source_line.product_id
            or locked_line.warehouse_id
            != source_line.warehouse_id
        ):
            raise SalesOrderFulfillmentReversalStateError(
                "Locked source line does not match "
                "fulfillment mapping"
            )

    # ---------------------------------------------------------
    # 5. VALIDATE CURRENT PERSISTENT BALANCE
    # ---------------------------------------------------------

    all_source_line_ids = [
        line.id
        for line in sales_order.lines
        if line.id is not None
    ]

    fulfilled_before = (
        await get_persisted_fulfilled_quantities(
            db,
            company_id=company_id,
            trade_document_id=sales_order.id,
            source_line_ids=all_source_line_ids,
            warehouse_document_type=(
                DocumentType.ISSUE
            ),
        )
    )

    reserved_before = (
        await get_outstanding_reservation_quantities(
            db,
            company_id=company_id,
            trade_document_id=sales_order.id,
            source_line_ids=all_source_line_ids,
        )
    )

    validate_sales_order_reversal_balances(
        sales_order,
        fulfilled_quantities=fulfilled_before,
        reserved_quantities=reserved_before,
    )

    for (
        source_line_id,
        reversal_quantity,
    ) in quantities_to_restore.items():
        if (
            reversal_quantity
            > fulfilled_before.get(
                source_line_id,
                ZERO,
            )
        ):
            raise SalesOrderFulfillmentReversalStateError(
                "Fulfillment reversal quantity exceeds "
                "currently posted fulfilled quantity"
            )

    # ---------------------------------------------------------
    # 6. REVERSE EXACT LINKED WAREHOUSE ISSUE
    # ---------------------------------------------------------

    try:
        reversed_document = (
            await reverse_document_for_trade_fulfillment(
                db,
                company_id=company_id,
                document_id=(
                    fulfillment.warehouse_document_id
                ),
                fulfillment_id=fulfillment.id,
                reversal_date=reversal_date,
                reversed_by=reversed_by,
            )
        )

    except DocumentReversalError as exc:
        raise SalesOrderFulfillmentReversalStateError(
            str(exc)
        ) from exc

    if (
        reversed_document.status
        != DocumentStatus.REVERSED
    ):
        raise SalesOrderFulfillmentReversalStateError(
            "Warehouse fulfillment document "
            "was not reversed"
        )

    # ---------------------------------------------------------
    # 7. RESTORE RESERVATIONS
    # ---------------------------------------------------------
    #
    # Physical stock has now been restored, so RESERVE can
    # validate against the post-reversal StockBalance.
    # ---------------------------------------------------------

    for source_line in ordered_source_lines:
        await reserve_source_line(
            db,
            company_id=company_id,
            source_document_id=sales_order.id,
            source_document_line_id=source_line.id,
            quantity=quantities_to_restore[
                source_line.id
            ],
        )

    # ---------------------------------------------------------
    # 8. RECALCULATE ACTIVE FULFILLMENT
    # ---------------------------------------------------------

    fulfilled_after = (
        await get_persisted_fulfilled_quantities(
            db,
            company_id=company_id,
            trade_document_id=sales_order.id,
            source_line_ids=all_source_line_ids,
            warehouse_document_type=(
                DocumentType.ISSUE
            ),
        )
    )

    reserved_after = (
        await get_outstanding_reservation_quantities(
            db,
            company_id=company_id,
            trade_document_id=sales_order.id,
            source_line_ids=all_source_line_ids,
        )
    )

    validate_sales_order_reversal_balances(
        sales_order,
        fulfilled_quantities=fulfilled_after,
        reserved_quantities=reserved_after,
    )

    sales_order.status = (
        calculate_sales_order_fulfillment_status(
            sales_order,
            fulfilled_after,
        )
    )

    await db.flush()

    return (
        SalesOrderFulfillmentReversalExecutionResult(
            sales_order=sales_order,
            warehouse_document=reversed_document,
            fulfillment=fulfillment,
        )
    )


# =============================================================
# PURCHASE ORDER FULFILLMENT REVERSAL
# =============================================================


class PurchaseOrderFulfillmentReversalNotFoundError(
    PurchaseOrderFulfillmentError
):
    """Requested Purchase Order fulfillment does not exist."""


class PurchaseOrderFulfillmentReversalStatusError(
    PurchaseOrderFulfillmentError
):
    """Purchase Order cannot reverse fulfillment in this state."""


class PurchaseOrderFulfillmentReversalStateError(
    PurchaseOrderFulfillmentError
):
    """Persistent Purchase Order fulfillment state is invalid."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseOrderFulfillmentReversalExecutionResult:
    """
    Result of one atomic Purchase Order receipt reversal.

    Caller owns COMMIT / ROLLBACK.
    """

    purchase_order: TradeDocument
    warehouse_document: Document
    fulfillment: TradeFulfillment


def validate_purchase_order_fulfillment_reversal_state(
    document: TradeDocument,
) -> None:
    """
    Purchase receipt fulfillment may be reversed only while
    the Purchase Order has active posted receipt fulfillment.
    """
    if (
        document.direction
        != TradeDirection.PURCHASE
    ):
        raise PurchaseOrderFulfillmentTypeError(
            "Only purchase trade documents can reverse "
            "purchase-order fulfillment"
        )

    if (
        document.kind
        != TradeDocumentKind.ORDER
    ):
        raise PurchaseOrderFulfillmentTypeError(
            "Only trade document kind 'order' can reverse "
            "purchase-order fulfillment"
        )

    if document.status not in (
        TradeDocumentStatus.PARTIALLY_FULFILLED,
        TradeDocumentStatus.FULFILLED,
    ):
        raise PurchaseOrderFulfillmentReversalStatusError(
            "Only partially fulfilled or fulfilled "
            "purchase orders can reverse fulfillment"
        )

    if not document.lines:
        raise PurchaseOrderFulfillmentLinesRequiredError(
            "Purchase order must contain at least one line"
        )


async def get_locked_purchase_trade_fulfillment(
    db: AsyncSession,
    *,
    company_id: int,
    trade_document_id: int,
    fulfillment_id: int,
) -> TradeFulfillment:
    """
    Lock one exact persistent fulfillment belonging to the
    requested Purchase Order and company.
    """
    result = await db.execute(
        select(
            TradeFulfillment
        )
        .where(
            TradeFulfillment.id
            == fulfillment_id,
            TradeFulfillment.company_id
            == company_id,
            TradeFulfillment.trade_document_id
            == trade_document_id,
        )
        .with_for_update()
    )

    fulfillment = (
        result.scalar_one_or_none()
    )

    if fulfillment is None:
        raise (
            PurchaseOrderFulfillmentReversalNotFoundError(
                "Trade fulfillment not found"
            )
        )

    return fulfillment


def validate_purchase_order_reversal_balances(
    document: TradeDocument,
    *,
    fulfilled_quantities: Mapping[
        int,
        Decimal,
    ],
) -> None:
    """
    Validate persistent Purchase Order fulfillment before
    reversal.

    Purchase invariant:

        0 <= active POSTED RECEIPT fulfillment
           <= source quantity

    Current lifecycle status must agree with that persistent
    fulfillment state.
    """
    for line in document.lines:
        if (
            line.id is None
            or line.id <= 0
        ):
            raise (
                PurchaseOrderFulfillmentReversalStateError(
                    "Purchase order contains an "
                    "unpersisted source line"
                )
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
            raise (
                PurchaseOrderFulfillmentReversalStateError(
                    "Fulfilled quantity cannot be negative: "
                    f"line_id={line.id}"
                )
            )

        if (
            fulfilled_quantity
            > source_quantity
        ):
            raise (
                PurchaseOrderFulfillmentReversalStateError(
                    "Persisted receipt fulfillment exceeds "
                    "source quantity: "
                    f"line_id={line.id}, "
                    f"source={source_quantity}, "
                    f"fulfilled={fulfilled_quantity}"
                )
            )

    calculated_status = (
        calculate_purchase_order_fulfillment_status(
            document,
            fulfilled_quantities,
        )
    )

    if (
        calculated_status
        != document.status
    ):
        raise (
            PurchaseOrderFulfillmentReversalStateError(
                "Purchase order lifecycle status does not "
                "match persistent posted receipt fulfillment: "
                f"stored={document.status.value}, "
                f"calculated={calculated_status.value}"
            )
        )


async def execute_purchase_order_fulfillment_reversal(
    db: AsyncSession,
    *,
    company_id: int,
    trade_document_id: int,
    fulfillment_id: int,
    reversal_date: date,
    reversed_by: int,
) -> PurchaseOrderFulfillmentReversalExecutionResult:
    """
    Reverse one persistent Purchase Order receipt fulfillment
    atomically.

    Caller owns COMMIT / ROLLBACK.

    Sequence:

        1. lock Purchase Order header
        2. validate Purchase Order lifecycle
        3. lock fulfillment header + mappings
        4. validate exact RECEIPT fulfillment mapping
        5. validate persistent POSTED receipt quantities
        6. reverse exact linked warehouse RECEIPT
        7. recalculate active POSTED receipt fulfillment
        8. verify exact fulfillment quantity disappeared
        9. recalculate Purchase Order lifecycle status
       10. flush

    Purchase fulfillment reversal deliberately performs no
    reservation operations.
    """
    if fulfillment_id <= 0:
        raise ValueError(
            "Fulfillment ID must be greater than zero"
        )

    # ---------------------------------------------------------
    # 1. LOCK PURCHASE ORDER
    # ---------------------------------------------------------

    purchase_order = await get_locked_purchase_order(
        db,
        company_id=company_id,
        document_id=trade_document_id,
    )

    validate_purchase_order_fulfillment_reversal_state(
        purchase_order
    )

    # ---------------------------------------------------------
    # 2. LOCK EXACT FULFILLMENT
    # ---------------------------------------------------------

    fulfillment = (
        await get_locked_purchase_trade_fulfillment(
            db,
            company_id=company_id,
            trade_document_id=purchase_order.id,
            fulfillment_id=fulfillment_id,
        )
    )

    if await has_active_fulfillment_allocations(
        db,
        company_id=company_id,
        fulfillment_id=fulfillment.id,
        lock_rows=True,
    ):
        raise PurchaseOrderFulfillmentReversalStateError(
            "Trade fulfillment has ACTIVE Invoice "
            "allocations; reverse matching allocations "
            "before fulfillment reversal"
        )

    if (
        fulfillment.warehouse_document_type
        != DocumentType.RECEIPT.value
    ):
        raise PurchaseOrderFulfillmentReversalStateError(
            "Purchase Order fulfillment target must be "
            "a warehouse RECEIPT"
        )

    mappings = (
        await get_trade_fulfillment_lines_for_reversal(
            db,
            company_id=company_id,
            trade_document_id=purchase_order.id,
            fulfillment_id=fulfillment.id,
        )
    )

    if not mappings:
        raise PurchaseOrderFulfillmentReversalStateError(
            "Trade fulfillment has no line mappings"
        )

    # ---------------------------------------------------------
    # 3. VALIDATE / AGGREGATE MAPPINGS
    # ---------------------------------------------------------

    source_lines_by_id = {
        line.id: line
        for line in purchase_order.lines
        if line.id is not None
    }

    reversal_quantities: dict[
        int,
        Decimal,
    ] = {}

    for mapping in mappings:
        if (
            mapping.warehouse_document_id
            != fulfillment.warehouse_document_id
        ):
            raise (
                PurchaseOrderFulfillmentReversalStateError(
                    "Fulfillment line points to a different "
                    "warehouse document"
                )
            )

        source_line = source_lines_by_id.get(
            mapping.trade_document_line_id
        )

        if source_line is None:
            raise (
                PurchaseOrderFulfillmentReversalStateError(
                    "Fulfillment mapping references a source "
                    "line outside the Purchase Order"
                )
            )

        if (
            source_line.product_id
            != mapping.product_id
        ):
            raise (
                PurchaseOrderFulfillmentReversalStateError(
                    "Fulfillment mapping product does not "
                    "match the source line"
                )
            )

        if (
            source_line.warehouse_id
            != mapping.warehouse_id
        ):
            raise (
                PurchaseOrderFulfillmentReversalStateError(
                    "Fulfillment mapping warehouse does not "
                    "match the source line"
                )
            )

        quantity = Decimal(
            mapping.quantity
        )

        if quantity <= ZERO:
            raise (
                PurchaseOrderFulfillmentReversalStateError(
                    "Fulfillment mapping quantity must be "
                    "greater than zero"
                )
            )

        reversal_quantities[
            source_line.id
        ] = (
            reversal_quantities.get(
                source_line.id,
                ZERO,
            )
            + quantity
        )

    all_source_line_ids = [
        line.id
        for line in purchase_order.lines
        if line.id is not None
    ]

    # ---------------------------------------------------------
    # 4. VALIDATE CURRENT ACTIVE POSTED RECEIPTS
    # ---------------------------------------------------------

    fulfilled_before = (
        await get_persisted_fulfilled_quantities(
            db,
            company_id=company_id,
            trade_document_id=purchase_order.id,
            source_line_ids=all_source_line_ids,
            warehouse_document_type=(
                DocumentType.RECEIPT
            ),
        )
    )

    validate_purchase_order_reversal_balances(
        purchase_order,
        fulfilled_quantities=fulfilled_before,
    )

    for (
        source_line_id,
        reversal_quantity,
    ) in reversal_quantities.items():
        if (
            reversal_quantity
            > fulfilled_before.get(
                source_line_id,
                ZERO,
            )
        ):
            raise (
                PurchaseOrderFulfillmentReversalStateError(
                    "Receipt fulfillment reversal quantity "
                    "exceeds currently posted fulfilled "
                    "quantity"
                )
            )

    # ---------------------------------------------------------
    # 5. REVERSE EXACT LINKED WAREHOUSE RECEIPT
    # ---------------------------------------------------------

    try:
        reversed_document = (
            await reverse_document_for_trade_fulfillment(
                db,
                company_id=company_id,
                document_id=(
                    fulfillment.warehouse_document_id
                ),
                fulfillment_id=fulfillment.id,
                reversal_date=reversal_date,
                reversed_by=reversed_by,
            )
        )

    except DocumentReversalError as exc:
        raise (
            PurchaseOrderFulfillmentReversalStateError(
                str(exc)
            )
        ) from exc

    if (
        reversed_document.status
        != DocumentStatus.REVERSED
    ):
        raise (
            PurchaseOrderFulfillmentReversalStateError(
                "Warehouse fulfillment RECEIPT "
                "was not reversed"
            )
        )

    if (
        reversed_document.document_type
        != DocumentType.RECEIPT
    ):
        raise (
            PurchaseOrderFulfillmentReversalStateError(
                "Controlled reversal returned a "
                "non-RECEIPT warehouse document"
            )
        )

    # ---------------------------------------------------------
    # 6. RECALCULATE ACTIVE POSTED RECEIPT FULFILLMENT
    # ---------------------------------------------------------

    fulfilled_after = (
        await get_persisted_fulfilled_quantities(
            db,
            company_id=company_id,
            trade_document_id=purchase_order.id,
            source_line_ids=all_source_line_ids,
            warehouse_document_type=(
                DocumentType.RECEIPT
            ),
        )
    )

    # Exact audit invariant:
    #
    # after = before - quantity represented by THIS
    # reversed fulfillment.
    for source_line in purchase_order.lines:
        if source_line.id is None:
            continue

        expected_after = (
            Decimal(
                fulfilled_before.get(
                    source_line.id,
                    ZERO,
                )
            )
            - Decimal(
                reversal_quantities.get(
                    source_line.id,
                    ZERO,
                )
            )
        )

        actual_after = Decimal(
            fulfilled_after.get(
                source_line.id,
                ZERO,
            )
        )

        if actual_after != expected_after:
            raise (
                PurchaseOrderFulfillmentReversalStateError(
                    "Active posted receipt fulfillment did "
                    "not decrease by the exact reversed "
                    "fulfillment quantity: "
                    f"line_id={source_line.id}, "
                    f"expected={expected_after}, "
                    f"actual={actual_after}"
                )
            )

    # ---------------------------------------------------------
    # 7. RECALCULATE PURCHASE ORDER STATUS
    # ---------------------------------------------------------

    purchase_order.status = (
        calculate_purchase_order_fulfillment_status(
            purchase_order,
            fulfilled_after,
        )
    )

    await db.flush()

    return (
        PurchaseOrderFulfillmentReversalExecutionResult(
            purchase_order=purchase_order,
            warehouse_document=reversed_document,
            fulfillment=fulfillment,
        )
    )
