from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import (
    Document,
    DocumentType,
)
from app.models.document_line import (
    DocumentLine,
)
from app.models.sales_return_cost_restoration_event import (
    SalesReturnCostRestorationEvent,
)
from app.models.sales_return_cost_restoration_fifo_slice import (
    SalesReturnCostRestorationFifoSlice,
)
from app.models.trade_return_event import (
    TradeReturnEvent,
)
from app.services.sales_return_cost_calculation_service import (
    SalesReturnCostCalculationError,
)
from app.services.sales_return_cost_restoration_journal_service import (
    SalesReturnCostRestorationJournalError,
    generate_and_post_sales_return_cost_restoration_journal_entry,
    reverse_sales_return_cost_restoration_journal_entry,
)
from app.services.sales_return_cost_restoration_reconciliation_service import (
    SalesReturnCostRestorationReconciliationError,
    SalesReturnCostRestorationReconciliationResult,
    reconcile_sales_return_cost_restoration_for_fulfillment_line,
)
from app.services.sales_return_stock_restoration_service import (
    SalesReturnStockRestorationError,
    restore_sales_return_physical_cost_state,
    reverse_sales_return_physical_cost_state,
)


class SalesReturnCostRestorationLifecycleError(
    Exception
):
    """Base Sales Return stock + COGS lifecycle error."""


class SalesReturnCostRestorationLifecycleSourceError(
    SalesReturnCostRestorationLifecycleError
):
    """Immutable return source cannot be resolved safely."""


@dataclass(
    frozen=True,
    slots=True,
)
class SalesReturnCostRestorationRuntimeContext:
    trade_return_event: TradeReturnEvent
    document: Document
    line: DocumentLine
    fifo_slices: tuple[
        SalesReturnCostRestorationFifoSlice,
        ...,
    ]


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


def _validate_lifecycle_context(
    *,
    company_id: int,
    fulfillment_id: int,
    fulfillment_line_id: int,
    adjustment_date: date,
    created_by: int,
) -> None:
    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if fulfillment_id <= 0:
        raise ValueError(
            "fulfillment_id must be greater than zero"
        )

    if fulfillment_line_id <= 0:
        raise ValueError(
            "fulfillment_line_id must be greater than zero"
        )

    if not isinstance(
        adjustment_date,
        date,
    ):
        raise ValueError(
            "adjustment_date must be a date"
        )

    if created_by <= 0:
        raise ValueError(
            "created_by must be greater than zero"
        )


async def _load_sales_return_cost_runtime_context(
    db: AsyncSession,
    *,
    company_id: int,
    event: SalesReturnCostRestorationEvent,
) -> SalesReturnCostRestorationRuntimeContext:
    if event.id is None or event.id <= 0:
        raise SalesReturnCostRestorationLifecycleSourceError(
            "Cost-restoration event must have "
            "a persistent positive ID"
        )

    if event.company_id != company_id:
        raise SalesReturnCostRestorationLifecycleSourceError(
            "Cost-restoration event company mismatch"
        )

    trade_return_event = (
        await db.execute(
            select(
                TradeReturnEvent
            )
            .where(
                TradeReturnEvent.company_id
                == company_id,
                TradeReturnEvent.id
                == event.trade_return_event_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if trade_return_event is None:
        raise SalesReturnCostRestorationLifecycleSourceError(
            "TradeReturnEvent source was not found"
        )

    if (
        _enum_value(
            trade_return_event.direction
        )
        != "sale"
    ):
        raise SalesReturnCostRestorationLifecycleSourceError(
            "Cost-restoration lifecycle requires "
            "a sales TradeReturnEvent"
        )

    if (
        _enum_value(
            trade_return_event.return_document_type
        )
        != "receipt"
    ):
        raise SalesReturnCostRestorationLifecycleSourceError(
            "Sales Return must reference "
            "a receipt warehouse document"
        )

    document = (
        await db.execute(
            select(
                Document
            )
            .where(
                Document.company_id
                == company_id,
                Document.id
                == trade_return_event.return_document_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if document is None:
        raise SalesReturnCostRestorationLifecycleSourceError(
            "Sales Return receipt document "
            "was not found"
        )

    if (
        document.document_type
        != DocumentType.RECEIPT
    ):
        raise SalesReturnCostRestorationLifecycleSourceError(
            "Sales Return warehouse document "
            "is not a RECEIPT"
        )

    line = (
        await db.execute(
            select(
                DocumentLine
            )
            .where(
                DocumentLine.document_id
                == document.id,
                DocumentLine.id
                == (
                    trade_return_event
                    .return_document_line_id
                ),
                DocumentLine.product_id
                == trade_return_event.product_id,
                DocumentLine.warehouse_id
                == trade_return_event.return_warehouse_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if line is None:
        raise SalesReturnCostRestorationLifecycleSourceError(
            "Sales Return receipt line "
            "was not found with exact provenance"
        )

    fifo_slices = tuple(
        (
            await db.scalars(
                select(
                    SalesReturnCostRestorationFifoSlice
                )
                .where(
                    (
                        SalesReturnCostRestorationFifoSlice
                        .sales_return_cost_restoration_event_id
                        == event.id
                    )
                )
                .order_by(
                    SalesReturnCostRestorationFifoSlice.id
                )
                .with_for_update()
            )
        ).all()
    )

    return (
        SalesReturnCostRestorationRuntimeContext(
            trade_return_event=(
                trade_return_event
            ),
            document=document,
            line=line,
            fifo_slices=fifo_slices,
        )
    )


async def _apply_created_sales_return_cost_restoration_events(
    db: AsyncSession,
    *,
    company_id: int,
    result: SalesReturnCostRestorationReconciliationResult,
    created_by: int,
) -> None:
    """
    Consume immutable cost-restoration events in EXACT
    persistence/reconciliation order.

    Reversal event:
        1. reverse valuation-specific physical cost state
        2. reverse Dr281 / Cr902 journal
           -> Dr902 / Cr281

    Original / replacement event:
        1. restore valuation-specific physical cost state
        2. post Dr281 / Cr902

    Events are NEVER re-sorted here.

    IMPORTANT:
    This is the inventory-COST + COGS lifecycle only.

    StockBalance quantity and StockLedger warehouse movement
    remain owned by the operational warehouse return flow.
    """

    for event in result.created_events:
        context = (
            await _load_sales_return_cost_runtime_context(
                db,
                company_id=company_id,
                event=event,
            )
        )

        if event.reversal_of_id is not None:
            await reverse_sales_return_physical_cost_state(
                db,
                document=context.document,
                line=context.line,
                reversal_event=event,
            )

            await reverse_sales_return_cost_restoration_journal_entry(
                db,
                reversal_event=event,
                reversed_by=created_by,
            )

            continue

        await restore_sales_return_physical_cost_state(
            db,
            document=context.document,
            line=context.line,
            trade_return_event=(
                context.trade_return_event
            ),
            cost_event=event,
            fifo_slices=(
                context.fifo_slices
            ),
        )

        await generate_and_post_sales_return_cost_restoration_journal_entry(
            db,
            event=event,
            created_by=created_by,
        )


async def reconcile_sales_return_cost_restoration_lifecycle_for_fulfillment_line(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_id: int,
    fulfillment_line_id: int,
    adjustment_date: date,
    created_by: int,
) -> SalesReturnCostRestorationReconciliationResult:
    """
    Reconcile and apply the internal Sales Return stock-cost
    and COGS lifecycle for one original sales fulfillment line.

    Flow:

        active TradeReturnEvent history
                    +
        original InventoryCostEntry / FIFO provenance
                    ↓
        cost-restoration reconciliation
                    ↓
        immutable SalesReturnCostRestorationEvent history
                    ↓
        exact-order physical COST state
                    ↓
        exact-order COGS GL state

    Original / replacement:
        physical historical cost restore
        Dr 281 / Cr 902

    Reversal:
        physical historical cost reversal
        Dr 902 / Cr 281

    This service intentionally does NOT:
    - create a TradeReturnEvent;
    - create/reverse warehouse return quantity;
    - mutate StockBalance directly;
    - create StockLedger directly;
    - create VAT/RK corrections;
    - COMMIT or ROLLBACK.

    The operational warehouse return flow remains responsible
    for quantity-side StockBalance / StockLedger consistency.

    Caller owns COMMIT / ROLLBACK.
    """

    _validate_lifecycle_context(
        company_id=company_id,
        fulfillment_id=fulfillment_id,
        fulfillment_line_id=(
            fulfillment_line_id
        ),
        adjustment_date=adjustment_date,
        created_by=created_by,
    )

    try:
        result = (
            await reconcile_sales_return_cost_restoration_for_fulfillment_line(
                db,
                company_id=company_id,
                fulfillment_id=fulfillment_id,
                fulfillment_line_id=(
                    fulfillment_line_id
                ),
                created_by=created_by,
                adjustment_date=(
                    adjustment_date
                ),
            )
        )
    except (
        SalesReturnCostCalculationError,
        SalesReturnCostRestorationReconciliationError,
    ) as exc:
        raise SalesReturnCostRestorationLifecycleError(
            "Sales Return cost-restoration "
            "reconciliation failed: "
            f"{exc}"
        ) from exc

    try:
        await _apply_created_sales_return_cost_restoration_events(
            db,
            company_id=company_id,
            result=result,
            created_by=created_by,
        )
    except (
        SalesReturnCostRestorationLifecycleSourceError,
        SalesReturnStockRestorationError,
        SalesReturnCostRestorationJournalError,
    ) as exc:
        raise SalesReturnCostRestorationLifecycleError(
            "Sales Return stock + COGS "
            "runtime application failed: "
            f"{exc}"
        ) from exc

    return result
