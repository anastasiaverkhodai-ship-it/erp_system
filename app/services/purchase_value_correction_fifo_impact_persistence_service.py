from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.purchase_value_correction_allocation_event import (
    PurchaseValueCorrectionAllocationEvent,
)
from app.models.purchase_value_correction_fifo_impact_event import (
    PurchaseValueCorrectionFifoImpactEvent,
)
from app.models.stock_lot import StockLot
from app.models.stock_lot_consumption import (
    StockLotConsumption,
)
from app.models.trade_fulfillment_line import (
    TradeFulfillmentLine,
)
from app.models.warehouse_transfer_valuation_layer import (
    WarehouseTransferValuationLayer,
)
from app.services.purchase_value_correction_fifo_impact_calculation_service import (
    PurchaseValueCorrectionFifoImpactTarget,
)


ZERO = Decimal("0")


class PurchaseValueCorrectionFifoImpactPersistenceError(
    Exception
):
    pass


class PurchaseValueCorrectionFifoImpactDataIntegrityError(
    PurchaseValueCorrectionFifoImpactPersistenceError
):
    pass


class PurchaseValueCorrectionFifoImpactSourceNotFoundError(
    PurchaseValueCorrectionFifoImpactPersistenceError
):
    pass


class PurchaseValueCorrectionFifoImpactSourceStateError(
    PurchaseValueCorrectionFifoImpactPersistenceError
):
    pass


class PurchaseValueCorrectionFifoImpactChronologyError(
    PurchaseValueCorrectionFifoImpactPersistenceError
):
    pass


def _enum_value(
    value,
) -> str:
    raw = getattr(
        value,
        "value",
        value,
    )

    return str(raw).lower()


def _decimal(
    value,
) -> Decimal:
    result = Decimal(
        value
    )

    if not result.is_finite():
        raise (
            PurchaseValueCorrectionFifoImpactDataIntegrityError(
                "Monetary/quantity value must be finite"
            )
        )

    return result


def _target_key(
    target: PurchaseValueCorrectionFifoImpactTarget,
) -> tuple:
    return (
        target.purchase_value_correction_allocation_event_id,
        target.stock_lot_id,
        target.destination_kind,
        target.stock_lot_consumption_id,
        target.issue_document_id,
        target.issue_document_line_id,
    )


def _event_key(
    event: PurchaseValueCorrectionFifoImpactEvent,
) -> tuple:
    return (
        event.purchase_value_correction_allocation_event_id,
        event.stock_lot_id,
        event.destination_kind,
        event.stock_lot_consumption_id,
        event.issue_document_id,
        event.issue_document_line_id,
    )


def _target_is_noop(
    target: PurchaseValueCorrectionFifoImpactTarget,
) -> bool:
    return (
        _decimal(
            target.original_base_amount
        )
        == _decimal(
            target.corrected_base_amount
        )
    )


def _validate_target_shape(
    target: PurchaseValueCorrectionFifoImpactTarget,
) -> None:
    if not isinstance(
        target,
        PurchaseValueCorrectionFifoImpactTarget,
    ):
        raise (
            PurchaseValueCorrectionFifoImpactDataIntegrityError(
                "target must be "
                "PurchaseValueCorrectionFifoImpactTarget"
            )
        )

    for name, value in (
        (
            "purchase_value_correction_allocation_event_id",
            target.purchase_value_correction_allocation_event_id,
        ),
        (
            "stock_lot_id",
            target.stock_lot_id,
        ),
        (
            "invoice_fulfillment_allocation_id",
            target.invoice_fulfillment_allocation_id,
        ),
    ):
        if (
            isinstance(
                value,
                bool,
            )
            or not isinstance(
                value,
                int,
            )
            or value <= 0
        ):
            raise (
                PurchaseValueCorrectionFifoImpactDataIntegrityError(
                    f"{name} must be a positive integer"
                )
            )

    if not isinstance(
        target.recognition_date,
        date,
    ):
        raise (
            PurchaseValueCorrectionFifoImpactDataIntegrityError(
                "recognition_date must be a date"
            )
        )

    quantity = _decimal(
        target.quantity
    )

    if quantity <= ZERO:
        raise (
            PurchaseValueCorrectionFifoImpactDataIntegrityError(
                "quantity must be positive"
            )
        )

    original = _decimal(
        target.original_base_amount
    )

    corrected = _decimal(
        target.corrected_base_amount
    )

    if (
        original < ZERO
        or corrected < ZERO
    ):
        raise (
            PurchaseValueCorrectionFifoImpactDataIntegrityError(
                "base amounts cannot be negative"
            )
        )

    if (
        not isinstance(
            target.currency_code,
            str,
        )
        or len(
            target.currency_code
        )
        != 3
    ):
        raise (
            PurchaseValueCorrectionFifoImpactDataIntegrityError(
                "currency_code must contain exactly "
                "3 characters"
            )
        )

    if target.destination_kind == "on_hand":
        if any(
            value is not None
            for value in (
                target.stock_lot_consumption_id,
                target.issue_document_id,
                target.issue_document_line_id,
            )
        ):
            raise (
                PurchaseValueCorrectionFifoImpactDataIntegrityError(
                    "on_hand target cannot contain "
                    "ISSUE provenance"
                )
            )

    elif target.destination_kind == "issued":
        for name, value in (
            (
                "stock_lot_consumption_id",
                target.stock_lot_consumption_id,
            ),
            (
                "issue_document_id",
                target.issue_document_id,
            ),
            (
                "issue_document_line_id",
                target.issue_document_line_id,
            ),
        ):
            if (
                isinstance(
                    value,
                    bool,
                )
                or not isinstance(
                    value,
                    int,
                )
                or value <= 0
            ):
                raise (
                    PurchaseValueCorrectionFifoImpactDataIntegrityError(
                        f"{name} must be a positive integer "
                        "for issued target"
                    )
                )

    else:
        raise (
            PurchaseValueCorrectionFifoImpactDataIntegrityError(
                "destination_kind must be issued or on_hand"
            )
        )


def _validate_history(
    events: tuple[
        PurchaseValueCorrectionFifoImpactEvent,
        ...,
    ],
) -> None:
    by_id = {
        event.id: event
        for event in events
    }

    if len(
        by_id
    ) != len(
        events
    ):
        raise (
            PurchaseValueCorrectionFifoImpactDataIntegrityError(
                "Duplicate FIFO impact event IDs"
            )
        )

    reversals_by_original = {}

    for event in events:
        if event.reversal_of_id is None:
            continue

        original = by_id.get(
            event.reversal_of_id
        )

        if original is None:
            raise (
                PurchaseValueCorrectionFifoImpactDataIntegrityError(
                    "FIFO impact reversal references "
                    "an unloaded original event"
                )
            )

        if original.reversal_of_id is not None:
            raise (
                PurchaseValueCorrectionFifoImpactDataIntegrityError(
                    "FIFO impact reversal cannot reverse "
                    "another reversal"
                )
            )

        if (
            event.reversal_of_id
            in reversals_by_original
        ):
            raise (
                PurchaseValueCorrectionFifoImpactDataIntegrityError(
                    "Multiple FIFO impact reversals "
                    "exist for one original"
                )
            )

        reversals_by_original[
            event.reversal_of_id
        ] = event

        if _event_key(
            event
        ) != _event_key(
            original
        ):
            raise (
                PurchaseValueCorrectionFifoImpactDataIntegrityError(
                    "FIFO impact reversal changed "
                    "source/destination provenance"
                )
            )

        if (
            _decimal(
                event.quantity
            )
            != _decimal(
                original.quantity
            )
            or _decimal(
                event.original_base_amount
            )
            != _decimal(
                original.original_base_amount
            )
            or _decimal(
                event.corrected_base_amount
            )
            != _decimal(
                original.corrected_base_amount
            )
            or event.currency_code
            != original.currency_code
        ):
            raise (
                PurchaseValueCorrectionFifoImpactDataIntegrityError(
                    "FIFO impact reversal changed "
                    "immutable economic snapshot"
                )
            )

        if (
            event.recognition_date
            < original.recognition_date
        ):
            raise (
                PurchaseValueCorrectionFifoImpactChronologyError(
                    "FIFO impact reversal predates "
                    "its original"
                )
            )


def _active_originals(
    events: tuple[
        PurchaseValueCorrectionFifoImpactEvent,
        ...,
    ],
) -> tuple[
    PurchaseValueCorrectionFifoImpactEvent,
    ...,
]:
    reversed_ids = {
        event.reversal_of_id
        for event in events
        if event.reversal_of_id is not None
    }

    return tuple(
        event
        for event in events
        if (
            event.reversal_of_id is None
            and event.id not in reversed_ids
        )
    )


def _event_matches_target(
    *,
    event: PurchaseValueCorrectionFifoImpactEvent,
    target: PurchaseValueCorrectionFifoImpactTarget,
) -> bool:
    return (
        _event_key(
            event
        )
        == _target_key(
            target
        )
        and _decimal(
            event.quantity
        )
        == _decimal(
            target.quantity
        )
        and _decimal(
            event.original_base_amount
        )
        == _decimal(
            target.original_base_amount
        )
        and _decimal(
            event.corrected_base_amount
        )
        == _decimal(
            target.corrected_base_amount
        )
        and event.currency_code.upper()
        == target.currency_code.upper()
        and event.recognition_date
        == target.recognition_date
    )


async def _load_history_for_update(
    db: AsyncSession,
    *,
    company_id: int,
    allocation_event_id: int,
) -> tuple[
    PurchaseValueCorrectionFifoImpactEvent,
    ...,
]:
    result = await db.execute(
        select(
            PurchaseValueCorrectionFifoImpactEvent
        )
        .where(
            PurchaseValueCorrectionFifoImpactEvent.company_id
            == company_id,
            PurchaseValueCorrectionFifoImpactEvent
            .purchase_value_correction_allocation_event_id
            == allocation_event_id,
        )
        .order_by(
            PurchaseValueCorrectionFifoImpactEvent.id
        )
        .with_for_update()
    )

    return tuple(
        result.scalars().all()
    )


def _transfer_destination_stock_lot_matches_allocation_source(
    *,
    company_id: int,
    transfer_layer: WarehouseTransferValuationLayer,
    destination_stock_lot: StockLot,
    source_consumption: StockLotConsumption,
    source_stock_lot: StockLot,
    fulfillment_line: TradeFulfillmentLine,
) -> bool:
    """
    Validate the immutable FIFO transfer provenance chain:

        allocation receipt line
          -> source StockLot
          -> source StockLotConsumption
          -> WarehouseTransferValuationLayer
          -> destination receipt line
          -> destination StockLot

    This is deliberately strict.

    It is NOT a generic permission for a FIFO impact to point
    at an arbitrary later StockLot.
    """

    return (
        transfer_layer.company_id
        == company_id
        and _enum_value(
            transfer_layer.valuation_method
        )
        == "fifo"
        and transfer_layer.source_stock_lot_consumption_id
        == source_consumption.id
        and transfer_layer.destination_receipt_document_id
        == destination_stock_lot.source_document_id
        and transfer_layer.destination_receipt_document_line_id
        == destination_stock_lot.source_document_line_id
        and transfer_layer.product_id
        == destination_stock_lot.product_id
        and transfer_layer.destination_warehouse_id
        == destination_stock_lot.warehouse_id
        and source_consumption.company_id
        == company_id
        and source_consumption.stock_lot_id
        == source_stock_lot.id
        and _decimal(
            source_consumption.quantity
        )
        == _decimal(
            transfer_layer.quantity
        )
        and source_stock_lot.company_id
        == company_id
        and source_stock_lot.product_id
        == destination_stock_lot.product_id
        and source_stock_lot.source_document_line_id
        == fulfillment_line.warehouse_document_line_id
    )


async def _validate_positive_target_sources(
    db: AsyncSession,
    *,
    company_id: int,
    target: PurchaseValueCorrectionFifoImpactTarget,
) -> None:
    allocation_event = (
        await db.execute(
            select(
                PurchaseValueCorrectionAllocationEvent
            )
            .where(
                PurchaseValueCorrectionAllocationEvent.company_id
                == company_id,
                PurchaseValueCorrectionAllocationEvent.id
                == (
                    target
                    .purchase_value_correction_allocation_event_id
                ),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if allocation_event is None:
        raise (
            PurchaseValueCorrectionFifoImpactSourceNotFoundError(
                "Purchase Value Correction allocation "
                "source was not found"
            )
        )

    if (
        allocation_event.invoice_fulfillment_allocation_id
        != target.invoice_fulfillment_allocation_id
    ):
        raise (
            PurchaseValueCorrectionFifoImpactDataIntegrityError(
                "FIFO impact IFA provenance does not match "
                "Purchase Value Correction allocation source"
            )
        )

    if allocation_event.reversal_of_id is not None:
        raise (
            PurchaseValueCorrectionFifoImpactSourceStateError(
                "Allocation reversal cannot be a "
                "positive FIFO impact source"
            )
        )

    allocation_reversal_id = (
        await db.execute(
            select(
                PurchaseValueCorrectionAllocationEvent.id
            ).where(
                PurchaseValueCorrectionAllocationEvent.company_id
                == company_id,
                PurchaseValueCorrectionAllocationEvent.reversal_of_id
                == allocation_event.id,
            )
        )
    ).scalar_one_or_none()

    if allocation_reversal_id is not None:
        raise (
            PurchaseValueCorrectionFifoImpactSourceStateError(
                "Allocation source is no longer active"
            )
        )

    if (
        allocation_event.currency_code.upper()
        != target.currency_code.upper()
    ):
        raise (
            PurchaseValueCorrectionFifoImpactDataIntegrityError(
                "FIFO impact currency does not match "
                "allocation source"
            )
        )

    if (
        target.recognition_date
        < allocation_event.recognition_date
    ):
        raise (
            PurchaseValueCorrectionFifoImpactChronologyError(
                "FIFO impact predates allocation source"
            )
        )

    if (
        _decimal(
            target.original_base_amount
        )
        > _decimal(
            allocation_event.original_allocated_base_amount
        )
        or _decimal(
            target.corrected_base_amount
        )
        > _decimal(
            allocation_event.corrected_allocated_base_amount
        )
    ):
        raise (
            PurchaseValueCorrectionFifoImpactDataIntegrityError(
                "FIFO impact amount exceeds "
                "allocation source amount"
            )
        )

    allocation = (
        await db.execute(
            select(
                InvoiceFulfillmentAllocation
            )
            .where(
                InvoiceFulfillmentAllocation.company_id
                == company_id,
                InvoiceFulfillmentAllocation.id
                == (
                    allocation_event
                    .invoice_fulfillment_allocation_id
                ),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if allocation is None:
        raise (
            PurchaseValueCorrectionFifoImpactSourceNotFoundError(
                "InvoiceFulfillmentAllocation was not found"
            )
        )

    if _enum_value(
        allocation.status
    ) != "active":
        raise (
            PurchaseValueCorrectionFifoImpactSourceStateError(
                "InvoiceFulfillmentAllocation "
                "is not ACTIVE"
            )
        )

    if (
        _decimal(
            target.quantity
        )
        > _decimal(
            allocation.quantity
        )
    ):
        raise (
            PurchaseValueCorrectionFifoImpactDataIntegrityError(
                "FIFO impact quantity exceeds "
                "InvoiceFulfillmentAllocation quantity"
            )
        )

    fulfillment_line = (
        await db.execute(
            select(
                TradeFulfillmentLine
            )
            .where(
                TradeFulfillmentLine.company_id
                == company_id,
                TradeFulfillmentLine.id
                == allocation.fulfillment_line_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if fulfillment_line is None:
        raise (
            PurchaseValueCorrectionFifoImpactSourceNotFoundError(
                "TradeFulfillmentLine was not found"
            )
        )

    stock_lot = (
        await db.execute(
            select(
                StockLot
            )
            .where(
                StockLot.company_id
                == company_id,
                StockLot.id
                == target.stock_lot_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if stock_lot is None:
        raise (
            PurchaseValueCorrectionFifoImpactSourceNotFoundError(
                "StockLot was not found"
            )
        )

    if (
        stock_lot.source_document_line_id
        != fulfillment_line.warehouse_document_line_id
    ):
        transfer_layers = (
            (
                await db.execute(
                    select(
                        WarehouseTransferValuationLayer
                    )
                    .where(
                        WarehouseTransferValuationLayer.company_id
                        == company_id,
                        (
                            WarehouseTransferValuationLayer
                            .destination_receipt_document_id
                        )
                        == stock_lot.source_document_id,
                        (
                            WarehouseTransferValuationLayer
                            .destination_receipt_document_line_id
                        )
                        == stock_lot.source_document_line_id,
                    )
                    .order_by(
                        WarehouseTransferValuationLayer.id
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )

        if len(
            transfer_layers
        ) != 1:
            raise (
                PurchaseValueCorrectionFifoImpactDataIntegrityError(
                    "StockLot does not belong to "
                    "allocation receipt line or one exact "
                    "FIFO warehouse-transfer provenance chain"
                )
            )

        transfer_layer = transfer_layers[
            0
        ]

        if (
            transfer_layer.source_stock_lot_consumption_id
            is None
        ):
            raise (
                PurchaseValueCorrectionFifoImpactDataIntegrityError(
                    "FIFO warehouse-transfer provenance "
                    "does not reference source "
                    "StockLotConsumption"
                )
            )

        source_consumption = (
            await db.execute(
                select(
                    StockLotConsumption
                )
                .where(
                    StockLotConsumption.company_id
                    == company_id,
                    StockLotConsumption.id
                    == (
                        transfer_layer
                        .source_stock_lot_consumption_id
                    ),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()

        if source_consumption is None:
            raise (
                PurchaseValueCorrectionFifoImpactSourceNotFoundError(
                    "FIFO warehouse-transfer source "
                    "StockLotConsumption was not found"
                )
            )

        source_stock_lot = (
            await db.execute(
                select(
                    StockLot
                )
                .where(
                    StockLot.company_id
                    == company_id,
                    StockLot.id
                    == source_consumption.stock_lot_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()

        if source_stock_lot is None:
            raise (
                PurchaseValueCorrectionFifoImpactSourceNotFoundError(
                    "FIFO warehouse-transfer source "
                    "StockLot was not found"
                )
            )

        if not (
            _transfer_destination_stock_lot_matches_allocation_source(
                company_id=company_id,
                transfer_layer=transfer_layer,
                destination_stock_lot=stock_lot,
                source_consumption=source_consumption,
                source_stock_lot=source_stock_lot,
                fulfillment_line=fulfillment_line,
            )
        ):
            raise (
                PurchaseValueCorrectionFifoImpactDataIntegrityError(
                    "StockLot warehouse-transfer provenance "
                    "does not lead back to allocation "
                    "receipt line"
                )
            )

    if (
        _decimal(
            target.quantity
        )
        > _decimal(
            stock_lot.original_quantity
        )
    ):
        raise (
            PurchaseValueCorrectionFifoImpactDataIntegrityError(
                "FIFO impact quantity exceeds "
                "StockLot original quantity"
            )
        )

    if target.destination_kind == "on_hand":
        if (
            _decimal(
                target.quantity
            )
            > _decimal(
                stock_lot.remaining_quantity
            )
        ):
            raise (
                PurchaseValueCorrectionFifoImpactSourceStateError(
                    "on_hand FIFO impact quantity exceeds "
                    "current StockLot remaining quantity"
                )
            )

        return

    consumption = (
        await db.execute(
            select(
                StockLotConsumption
            )
            .where(
                StockLotConsumption.company_id
                == company_id,
                StockLotConsumption.id
                == target.stock_lot_consumption_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if consumption is None:
        raise (
            PurchaseValueCorrectionFifoImpactSourceNotFoundError(
                "StockLotConsumption was not found"
            )
        )

    if (
        consumption.stock_lot_id
        != target.stock_lot_id
        or consumption.issue_document_id
        != target.issue_document_id
        or consumption.issue_document_line_id
        != target.issue_document_line_id
    ):
        raise (
            PurchaseValueCorrectionFifoImpactDataIntegrityError(
                "Issued FIFO impact provenance does not "
                "match StockLotConsumption"
            )
        )

    if (
        _decimal(
            target.quantity
        )
        > _decimal(
            consumption.quantity
        )
    ):
        raise (
            PurchaseValueCorrectionFifoImpactDataIntegrityError(
                "Issued FIFO impact quantity exceeds "
                "StockLotConsumption quantity"
            )
        )

    issue_document = (
        await db.execute(
            select(
                Document
            )
            .where(
                Document.company_id
                == company_id,
                Document.id
                == target.issue_document_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if issue_document is None:
        raise (
            PurchaseValueCorrectionFifoImpactSourceNotFoundError(
                "ISSUE Document was not found"
            )
        )

    if (
        _enum_value(
            issue_document.status
        )
        != "posted"
        or _enum_value(
            issue_document.document_type
        )
        != "issue"
    ):
        raise (
            PurchaseValueCorrectionFifoImpactSourceStateError(
                "FIFO consumption ISSUE document "
                "is not an active POSTED ISSUE"
            )
        )

    if (
        target.recognition_date
        < issue_document.document_date
    ):
        raise (
            PurchaseValueCorrectionFifoImpactChronologyError(
                "Issued FIFO impact predates ISSUE document"
            )
        )


def _new_event_from_target(
    *,
    company_id: int,
    target: PurchaseValueCorrectionFifoImpactTarget,
    created_by: int,
    recognition_date: date,
) -> PurchaseValueCorrectionFifoImpactEvent:
    return (
        PurchaseValueCorrectionFifoImpactEvent(
            company_id=company_id,
            purchase_value_correction_allocation_event_id=(
                target
                .purchase_value_correction_allocation_event_id
            ),
            stock_lot_id=target.stock_lot_id,
            destination_kind=target.destination_kind,
            stock_lot_consumption_id=(
                target.stock_lot_consumption_id
            ),
            issue_document_id=(
                target.issue_document_id
            ),
            issue_document_line_id=(
                target.issue_document_line_id
            ),
            recognition_date=recognition_date,
            quantity=_decimal(
                target.quantity
            ),
            original_base_amount=_decimal(
                target.original_base_amount
            ),
            corrected_base_amount=_decimal(
                target.corrected_base_amount
            ),
            currency_code=(
                target.currency_code.upper()
            ),
            created_by=created_by,
            reversal_of_id=None,
        )
    )


def _new_reversal(
    *,
    original: PurchaseValueCorrectionFifoImpactEvent,
    created_by: int,
    reversal_date: date,
) -> PurchaseValueCorrectionFifoImpactEvent:
    return (
        PurchaseValueCorrectionFifoImpactEvent(
            company_id=original.company_id,
            purchase_value_correction_allocation_event_id=(
                original
                .purchase_value_correction_allocation_event_id
            ),
            stock_lot_id=original.stock_lot_id,
            destination_kind=original.destination_kind,
            stock_lot_consumption_id=(
                original.stock_lot_consumption_id
            ),
            issue_document_id=(
                original.issue_document_id
            ),
            issue_document_line_id=(
                original.issue_document_line_id
            ),
            recognition_date=reversal_date,
            quantity=_decimal(
                original.quantity
            ),
            original_base_amount=_decimal(
                original.original_base_amount
            ),
            corrected_base_amount=_decimal(
                original.corrected_base_amount
            ),
            currency_code=original.currency_code,
            created_by=created_by,
            reversal_of_id=original.id,
        )
    )


async def reconcile_purchase_value_correction_fifo_impact_source(
    db: AsyncSession,
    *,
    company_id: int,
    target: PurchaseValueCorrectionFifoImpactTarget,
    created_by: int,
    reversal_date: date | None = None,
) -> tuple[
    PurchaseValueCorrectionFifoImpactEvent,
    ...,
]:
    """
    Reconcile one FIFO destination source.

    Rules:

    no current + target no-op:
        nothing.

    no current + positive target:
        persist one original event on target recognition_date.

    exact active current:
        nothing.

    active current + desired no-op:
        reversal only.

    active current + changed quantity/amount/date:
        reversal + complete replacement.

    Any change of an existing active source requires explicit
    reversal_date. Replacement is recognized on that same
    forward date.

    Service never COMMITs or ROLLBACKs.
    Caller owns the transaction.
    """

    if (
        isinstance(
            company_id,
            bool,
        )
        or not isinstance(
            company_id,
            int,
        )
        or company_id <= 0
    ):
        raise (
            PurchaseValueCorrectionFifoImpactDataIntegrityError(
                "company_id must be a positive integer"
            )
        )

    if (
        isinstance(
            created_by,
            bool,
        )
        or not isinstance(
            created_by,
            int,
        )
        or created_by <= 0
    ):
        raise (
            PurchaseValueCorrectionFifoImpactDataIntegrityError(
                "created_by must be a positive integer"
            )
        )

    _validate_target_shape(
        target
    )

    history = await _load_history_for_update(
        db,
        company_id=company_id,
        allocation_event_id=(
            target
            .purchase_value_correction_allocation_event_id
        ),
    )

    _validate_history(
        history
    )

    key = _target_key(
        target
    )

    active_matches = tuple(
        event
        for event in _active_originals(
            history
        )
        if _event_key(
            event
        )
        == key
    )

    if len(
        active_matches
    ) > 1:
        raise (
            PurchaseValueCorrectionFifoImpactDataIntegrityError(
                "Multiple active FIFO impact originals "
                "exist for one destination key"
            )
        )

    current = (
        active_matches[0]
        if active_matches
        else None
    )

    target_noop = _target_is_noop(
        target
    )

    if current is None:
        if target_noop:
            return ()

        await _validate_positive_target_sources(
            db,
            company_id=company_id,
            target=target,
        )

        original = _new_event_from_target(
            company_id=company_id,
            target=target,
            created_by=created_by,
            recognition_date=(
                target.recognition_date
            ),
        )

        db.add(
            original
        )

        await db.flush()

        return (
            original,
        )

    if (
        not target_noop
        and _event_matches_target(
            event=current,
            target=target,
        )
    ):
        return ()

    if reversal_date is None:
        raise (
            PurchaseValueCorrectionFifoImpactChronologyError(
                "Changing an active FIFO impact requires "
                "explicit reversal_date"
            )
        )

    if not isinstance(
        reversal_date,
        date,
    ):
        raise (
            PurchaseValueCorrectionFifoImpactChronologyError(
                "reversal_date must be a date"
            )
        )

    if (
        reversal_date
        < current.recognition_date
    ):
        raise (
            PurchaseValueCorrectionFifoImpactChronologyError(
                "FIFO impact reversal cannot predate "
                "the active original"
            )
        )

    reversal = _new_reversal(
        original=current,
        created_by=created_by,
        reversal_date=reversal_date,
    )

    db.add(
        reversal
    )

    await db.flush()

    if target_noop:
        return (
            reversal,
        )

    await _validate_positive_target_sources(
        db,
        company_id=company_id,
        target=target,
    )

    if (
        reversal_date
        < target.recognition_date
    ):
        raise (
            PurchaseValueCorrectionFifoImpactChronologyError(
                "Forward replacement date cannot predate "
                "the desired primary recognition_date"
            )
        )

    replacement = _new_event_from_target(
        company_id=company_id,
        target=target,
        created_by=created_by,
        recognition_date=reversal_date,
    )

    db.add(
        replacement
    )

    await db.flush()

    return (
        reversal,
        replacement,
    )
