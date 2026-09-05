from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer_advance_clearing_event import (
    CustomerAdvanceClearingEvent,
)
from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.sales_recognition_event import (
    SalesRecognitionEvent,
)
from app.models.stock_ledger import (
    StockLedger,
)
from app.models.trade_return_event import (
    TradeReturnEvent,
)
from app.services.customer_advance_clearing_lifecycle_service import (
    CustomerAdvanceClearingLifecycleError,
    reconcile_customer_advance_clearing_lifecycle_for_invoice,
)
from app.services.sales_return_cost_restoration_lifecycle_service import (
    SalesReturnCostRestorationLifecycleError,
    reconcile_sales_return_cost_restoration_lifecycle_for_fulfillment_line,
)
from app.services.sales_return_cost_restoration_reconciliation_service import (
    SalesReturnCostRestorationReconciliationResult,
)
from app.services.sales_return_recognition_lifecycle_service import (
    SalesReturnRecognitionLifecycleError,
    reconcile_sales_return_recognition_lifecycle_for_fulfillment_line,
)
from app.services.sales_return_recognition_reconciliation_service import (
    SalesReturnRecognitionReconciliationResult,
)
from app.services.sales_return_warehouse_quantity_service import (
    SalesReturnWarehouseQuantityError,
    apply_sales_return_warehouse_quantity_event,
)


class SalesReturnOperationalError(
    Exception
):
    """Base atomic Sales Return operational error."""


class SalesReturnOperationalNotFoundError(
    SalesReturnOperationalError
):
    """Requested immutable TradeReturnEvent does not exist."""


class SalesReturnOperationalSourceError(
    SalesReturnOperationalError
):
    """TradeReturnEvent cannot drive the operational flow."""


@dataclass(
    frozen=True,
    slots=True,
)
class SalesReturnOperationalResult:
    trade_return_event: TradeReturnEvent
    quantity_movement: StockLedger
    economic_result: (
        SalesReturnRecognitionReconciliationResult
    )
    cost_result: (
        SalesReturnCostRestorationReconciliationResult
    )


def _enum_value(
    value,
) -> str:
    return str(
        getattr(
            value,
            "value",
            value,
        )
    ).strip().lower()


def _validate_operational_event(
    *,
    company_id: int,
    event: TradeReturnEvent,
    created_by: int,
) -> None:
    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if created_by <= 0:
        raise ValueError(
            "created_by must be greater than zero"
        )

    if (
        event.id is None
        or event.id <= 0
    ):
        raise SalesReturnOperationalSourceError(
            "TradeReturnEvent must have "
            "a persistent positive ID"
        )

    if event.company_id != company_id:
        raise SalesReturnOperationalSourceError(
            "TradeReturnEvent company mismatch"
        )

    if (
        _enum_value(
            event.direction
        )
        != "sale"
    ):
        raise SalesReturnOperationalSourceError(
            "Operational Sales Return requires "
            "a sales TradeReturnEvent"
        )

    if (
        _enum_value(
            event.return_document_type
        )
        != "receipt"
    ):
        raise SalesReturnOperationalSourceError(
            "Operational Sales Return requires "
            "a RECEIPT warehouse target"
        )

    if (
        event.original_fulfillment_id is None
        or event.original_fulfillment_id <= 0
    ):
        raise SalesReturnOperationalSourceError(
            "TradeReturnEvent original_fulfillment_id "
            "must be positive"
        )

    if (
        event.original_fulfillment_line_id is None
        or event.original_fulfillment_line_id <= 0
    ):
        raise SalesReturnOperationalSourceError(
            "TradeReturnEvent original_fulfillment_line_id "
            "must be positive"
        )

    if event.return_date is None:
        raise SalesReturnOperationalSourceError(
            "TradeReturnEvent return_date is required"
        )


async def _load_sales_return_operational_event(
    db: AsyncSession,
    *,
    company_id: int,
    trade_return_event_id: int,
) -> TradeReturnEvent:
    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if trade_return_event_id <= 0:
        raise ValueError(
            "trade_return_event_id "
            "must be greater than zero"
        )

    event = (
        await db.execute(
            select(
                TradeReturnEvent
            )
            .where(
                TradeReturnEvent.company_id
                == company_id,
                TradeReturnEvent.id
                == trade_return_event_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if event is None:
        raise SalesReturnOperationalNotFoundError(
            "TradeReturnEvent was not found"
        )

    return event


async def _load_impacted_customer_advance_invoice_ids(
    db: AsyncSession,
    *,
    company_id: int,
    economic_result: SalesReturnRecognitionReconciliationResult,
) -> tuple[int, ...]:
    """
    Resolve Sales Invoices changed by the current Sales Return
    economic transition that already have immutable customer
    advance clearing history.

    Existing payment / fulfillment lifecycle hooks own initial
    customer-advance clearing creation.

    Sales Return only re-reconciles already-existing clearing
    against the new net economic 361 capacity.
    """

    created_events = tuple(
        getattr(
            economic_result,
            "created_events",
            (),
        )
        or ()
    )

    if not created_events:
        return ()

    sales_source_ids = set()

    for created_event in created_events:
        source_id = getattr(
            created_event,
            "sales_recognition_event_id",
            None,
        )

        if (
            isinstance(
                source_id,
                bool,
            )
            or not isinstance(
                source_id,
                int,
            )
            or source_id <= 0
        ):
            raise SalesReturnOperationalSourceError(
                "Created Sales Return recognition event "
                "has invalid sales_recognition_event_id"
            )

        sales_source_ids.add(
            source_id
        )

    source_rows = (
        await db.execute(
            select(
                SalesRecognitionEvent.id,
                InvoiceFulfillmentAllocation.invoice_id,
            )
            .join(
                InvoiceFulfillmentAllocation,
                (
                    (
                        InvoiceFulfillmentAllocation.company_id
                        == SalesRecognitionEvent.company_id
                    )
                    & (
                        InvoiceFulfillmentAllocation.id
                        == (
                            SalesRecognitionEvent
                            .invoice_fulfillment_allocation_id
                        )
                    )
                ),
            )
            .where(
                SalesRecognitionEvent.company_id
                == company_id,
                InvoiceFulfillmentAllocation.company_id
                == company_id,
                SalesRecognitionEvent.id.in_(
                    tuple(
                        sorted(
                            sales_source_ids
                        )
                    )
                ),
            )
            .order_by(
                SalesRecognitionEvent.id
            )
        )
    ).all()

    resolved_source_ids = {
        int(
            row[0]
        )
        for row in source_rows
    }

    if (
        resolved_source_ids
        != sales_source_ids
    ):
        raise SalesReturnOperationalSourceError(
            "Could not resolve every Sales Return "
            "economic source to a Sales Invoice"
        )

    impacted_invoice_ids = {
        int(
            row[1]
        )
        for row in source_rows
    }

    if not impacted_invoice_ids:
        return ()

    history_invoice_ids = (
        (
            await db.execute(
                select(
                    InvoiceFulfillmentAllocation.invoice_id
                )
                .select_from(
                    CustomerAdvanceClearingEvent
                )
                .join(
                    SalesRecognitionEvent,
                    (
                        (
                            SalesRecognitionEvent.company_id
                            == (
                                CustomerAdvanceClearingEvent
                                .company_id
                            )
                        )
                        & (
                            SalesRecognitionEvent.id
                            == (
                                CustomerAdvanceClearingEvent
                                .sales_recognition_event_id
                            )
                        )
                    ),
                )
                .join(
                    InvoiceFulfillmentAllocation,
                    (
                        (
                            InvoiceFulfillmentAllocation.company_id
                            == SalesRecognitionEvent.company_id
                        )
                        & (
                            InvoiceFulfillmentAllocation.id
                            == (
                                SalesRecognitionEvent
                                .invoice_fulfillment_allocation_id
                            )
                        )
                    ),
                )
                .where(
                    CustomerAdvanceClearingEvent.company_id
                    == company_id,
                    SalesRecognitionEvent.company_id
                    == company_id,
                    InvoiceFulfillmentAllocation.company_id
                    == company_id,
                    InvoiceFulfillmentAllocation.invoice_id.in_(
                        tuple(
                            sorted(
                                impacted_invoice_ids
                            )
                        )
                    ),
                )
                .distinct()
                .order_by(
                    InvoiceFulfillmentAllocation.invoice_id
                )
            )
        )
        .scalars()
        .all()
    )

    result = []

    for invoice_id in history_invoice_ids:
        if (
            isinstance(
                invoice_id,
                bool,
            )
            or not isinstance(
                invoice_id,
                int,
            )
            or invoice_id <= 0
        ):
            raise SalesReturnOperationalSourceError(
                "Customer advance history resolved "
                "invalid invoice_id"
            )

        result.append(
            invoice_id
        )

    return tuple(
        result
    )


async def _apply_loaded_sales_return_operational_event(
    db: AsyncSession,
    *,
    company_id: int,
    event: TradeReturnEvent,
    created_by: int,
) -> SalesReturnOperationalResult:
    """
    Apply one already-persisted immutable TradeReturnEvent.

    Exact orchestration order:

      1. Warehouse QUANTITY
         StockBalance + StockLedger only.

      2. Economic Sales Return
         immutable recognition reconciliation + GL:
             original: Dr704 / Cr361
             reversal: Dr361 / Cr704

      3. Customer advance reconciliation
         Existing Dr681 / Cr361 clearing is rebuilt against
         net economic 361 capacity after Sales Return.

      4. Historical inventory COST + COGS
         FIFO / moving-average cost state +
             original: Dr281 / Cr902
             reversal: Dr902 / Cr281

    event.return_date is the single adjustment date for all
    downstream immutable accounting/cost transitions.

    No generic post_document().
    No generic document reversal.
    No COMMIT / ROLLBACK.

    Caller owns the transaction; therefore failure of step
    2, 3, or 4 rolls step 1 back with the same transaction.
    """

    _validate_operational_event(
        company_id=company_id,
        event=event,
        created_by=created_by,
    )

    try:
        quantity_movement = (
            await apply_sales_return_warehouse_quantity_event(
                db,
                event=event,
            )
        )
    except SalesReturnWarehouseQuantityError as exc:
        raise SalesReturnOperationalError(
            "Sales Return warehouse quantity "
            "application failed: "
            f"{exc}"
        ) from exc

    try:
        economic_result = (
            await reconcile_sales_return_recognition_lifecycle_for_fulfillment_line(
                db,
                company_id=company_id,
                fulfillment_id=(
                    event.original_fulfillment_id
                ),
                fulfillment_line_id=(
                    event.original_fulfillment_line_id
                ),
                adjustment_date=event.return_date,
                created_by=created_by,
            )
        )
    except SalesReturnRecognitionLifecycleError as exc:
        raise SalesReturnOperationalError(
            "Sales Return economic lifecycle failed: "
            f"{exc}"
        ) from exc

    try:
        customer_advance_invoice_ids = (
            await _load_impacted_customer_advance_invoice_ids(
                db,
                company_id=company_id,
                economic_result=economic_result,
            )
        )

        for invoice_id in customer_advance_invoice_ids:
            await reconcile_customer_advance_clearing_lifecycle_for_invoice(
                db,
                company_id=company_id,
                invoice_id=invoice_id,
                adjustment_date=event.return_date,
                created_by=created_by,
            )

    except CustomerAdvanceClearingLifecycleError as exc:
        raise SalesReturnOperationalError(
            "Sales Return customer advance "
            "reconciliation failed: "
            f"{exc}"
        ) from exc

    try:
        cost_result = (
            await reconcile_sales_return_cost_restoration_lifecycle_for_fulfillment_line(
                db,
                company_id=company_id,
                fulfillment_id=(
                    event.original_fulfillment_id
                ),
                fulfillment_line_id=(
                    event.original_fulfillment_line_id
                ),
                adjustment_date=event.return_date,
                created_by=created_by,
            )
        )
    except SalesReturnCostRestorationLifecycleError as exc:
        raise SalesReturnOperationalError(
            "Sales Return cost + COGS lifecycle failed: "
            f"{exc}"
        ) from exc

    return SalesReturnOperationalResult(
        trade_return_event=event,
        quantity_movement=quantity_movement,
        economic_result=economic_result,
        cost_result=cost_result,
    )


async def apply_sales_return_operational_event(
    db: AsyncSession,
    *,
    company_id: int,
    trade_return_event_id: int,
    created_by: int,
) -> SalesReturnOperationalResult:
    """
    Public event-level Sales Return operational entry point.

    The TradeReturnEvent must already exist durably. This
    service locks it and coordinates all three operational
    contours in one caller-owned transaction.

    This service does NOT create TradeReturnEvent.
    """

    if created_by <= 0:
        raise ValueError(
            "created_by must be greater than zero"
        )

    event = await _load_sales_return_operational_event(
        db,
        company_id=company_id,
        trade_return_event_id=(
            trade_return_event_id
        ),
    )

    return (
        await _apply_loaded_sales_return_operational_event(
            db,
            company_id=company_id,
            event=event,
            created_by=created_by,
        )
    )
