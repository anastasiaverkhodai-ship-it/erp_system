from dataclasses import (
    dataclass,
    replace,
)
from datetime import (
    date,
    datetime,
)
from decimal import Decimal

from sqlalchemy import (
    and_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import (
    Document,
    DocumentStatus,
    DocumentType,
)
from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
    InvoiceFulfillmentAllocationStatus,
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
from app.services.purchase_value_correction_fifo_impact_calculation_service import (
    ActiveFifoAllocationPeerCandidate,
    ActiveFifoConsumptionCandidate,
    PurchaseValueCorrectionFifoAllocationCandidate,
    FifoTransferRoutedDestinationSlice,
    PurchaseValueCorrectionFifoImpactTarget,
    build_purchase_value_correction_fifo_impact_targets,
)

from app.services.purchase_value_correction_fifo_transfer_orchestration_service import (
    PurchaseValueCorrectionFifoTransferRouter,
    preload_purchase_value_correction_fifo_transfer_router,
)
from app.services.purchase_value_correction_fifo_impact_persistence_service import (
    _active_originals as _active_impact_originals,
    _validate_history as _validate_impact_history,
    reconcile_purchase_value_correction_fifo_impact_source,
)


ZERO = Decimal("0")


class PurchaseValueCorrectionFifoImpactReconciliationError(
    Exception
):
    pass


class PurchaseValueCorrectionFifoImpactReconciliationNotFoundError(
    PurchaseValueCorrectionFifoImpactReconciliationError
):
    pass


class PurchaseValueCorrectionFifoImpactReconciliationDataIntegrityError(
    PurchaseValueCorrectionFifoImpactReconciliationError
):
    pass


class PurchaseValueCorrectionFifoImpactReconciliationSourceStateError(
    PurchaseValueCorrectionFifoImpactReconciliationError
):
    pass


class PurchaseValueCorrectionFifoImpactReconciliationChronologyError(
    PurchaseValueCorrectionFifoImpactReconciliationError
):
    pass


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionFifoImpactReconciliationResult:
    fulfillment_line_id: int
    stock_lot_id: int
    active_allocation_peer_ids: tuple[
        int,
        ...,
    ]
    active_correction_allocation_event_ids: tuple[
        int,
        ...,
    ]
    desired_targets: tuple[
        PurchaseValueCorrectionFifoImpactTarget,
        ...,
    ]
    reconciliation_targets: tuple[
        PurchaseValueCorrectionFifoImpactTarget,
        ...,
    ]
    created_events: tuple[
        PurchaseValueCorrectionFifoImpactEvent,
        ...,
    ]


def _positive_id(
    value: int,
    *,
    field: str,
) -> int:
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
            PurchaseValueCorrectionFifoImpactReconciliationDataIntegrityError(
                f"{field} must be a positive integer"
            )
        )

    return value


def _business_date(
    value: date,
    *,
    field: str,
) -> date:
    if (
        not isinstance(
            value,
            date,
        )
        or isinstance(
            value,
            datetime,
        )
    ):
        raise (
            PurchaseValueCorrectionFifoImpactReconciliationDataIntegrityError(
                f"{field} must be a date"
            )
        )

    return value


def _decimal(
    value,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(
            value
        )
    except Exception as exc:
        raise (
            PurchaseValueCorrectionFifoImpactReconciliationDataIntegrityError(
                f"{field} must be numeric"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseValueCorrectionFifoImpactReconciliationDataIntegrityError(
                f"{field} must be finite"
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
            target.original_base_amount,
            field="target original base",
        )
        == _decimal(
            target.corrected_base_amount,
            field="target corrected base",
        )
    )


def _same_state_except_date(
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
            event.quantity,
            field="event quantity",
        )
        == _decimal(
            target.quantity,
            field="target quantity",
        )
        and _decimal(
            event.original_base_amount,
            field="event original base",
        )
        == _decimal(
            target.original_base_amount,
            field="target original base",
        )
        and _decimal(
            event.corrected_base_amount,
            field="event corrected base",
        )
        == _decimal(
            target.corrected_base_amount,
            field="target corrected base",
        )
        and event.currency_code.upper()
        == target.currency_code.upper()
    )


async def _lock_fulfillment_line_and_receipt(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_line_id: int,
) -> tuple[
    TradeFulfillmentLine,
    Document,
]:
    line = (
        await db.execute(
            select(
                TradeFulfillmentLine
            )
            .where(
                TradeFulfillmentLine.company_id
                == company_id,
                TradeFulfillmentLine.id
                == fulfillment_line_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if line is None:
        raise (
            PurchaseValueCorrectionFifoImpactReconciliationNotFoundError(
                "TradeFulfillmentLine was not found"
            )
        )

    receipt = (
        await db.execute(
            select(
                Document
            )
            .where(
                Document.company_id
                == company_id,
                Document.id
                == line.warehouse_document_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if receipt is None:
        raise (
            PurchaseValueCorrectionFifoImpactReconciliationNotFoundError(
                "Receipt Document was not found"
            )
        )

    if (
        receipt.status
        != DocumentStatus.POSTED
        or receipt.document_type
        != DocumentType.RECEIPT
    ):
        raise (
            PurchaseValueCorrectionFifoImpactReconciliationSourceStateError(
                "Fulfillment warehouse source is not "
                "a POSTED RECEIPT"
            )
        )

    return (
        line,
        receipt,
    )


async def _lock_stock_lot(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_line: TradeFulfillmentLine,
) -> StockLot:
    lot = (
        await db.execute(
            select(
                StockLot
            )
            .where(
                StockLot.company_id
                == company_id,
                StockLot.source_document_line_id
                == fulfillment_line.warehouse_document_line_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if lot is None:
        raise (
            PurchaseValueCorrectionFifoImpactReconciliationNotFoundError(
                "FIFO StockLot for fulfillment receipt "
                "line was not found"
            )
        )

    if (
        lot.source_document_id
        != fulfillment_line.warehouse_document_id
        or lot.product_id
        != fulfillment_line.product_id
        or lot.warehouse_id
        != fulfillment_line.warehouse_id
    ):
        raise (
            PurchaseValueCorrectionFifoImpactReconciliationDataIntegrityError(
                "StockLot receipt provenance does not "
                "match TradeFulfillmentLine"
            )
        )

    lot_quantity = _decimal(
        lot.original_quantity,
        field="StockLot original_quantity",
    )

    fulfillment_quantity = _decimal(
        fulfillment_line.quantity,
        field="TradeFulfillmentLine quantity",
    )

    if (
        lot_quantity
        != fulfillment_quantity
    ):
        raise (
            PurchaseValueCorrectionFifoImpactReconciliationDataIntegrityError(
                "StockLot quantity does not match "
                "fulfillment receipt quantity"
            )
        )

    remaining = _decimal(
        lot.remaining_quantity,
        field="StockLot remaining_quantity",
    )

    if (
        remaining < ZERO
        or remaining > lot_quantity
    ):
        raise (
            PurchaseValueCorrectionFifoImpactReconciliationDataIntegrityError(
                "StockLot remaining quantity is invalid"
            )
        )

    return lot


async def _load_active_ifa_peers_for_update(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_line_id: int,
) -> tuple[
    InvoiceFulfillmentAllocation,
    ...,
]:
    rows = (
        await db.execute(
            select(
                InvoiceFulfillmentAllocation
            )
            .where(
                InvoiceFulfillmentAllocation.company_id
                == company_id,
                InvoiceFulfillmentAllocation.fulfillment_line_id
                == fulfillment_line_id,
                InvoiceFulfillmentAllocation.status
                == InvoiceFulfillmentAllocationStatus.ACTIVE,
            )
            .order_by(
                InvoiceFulfillmentAllocation.id
            )
            .with_for_update()
        )
    ).scalars().all()

    return tuple(
        rows
    )


async def _load_pvca_history_for_update(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_line_id: int,
) -> tuple[
    PurchaseValueCorrectionAllocationEvent,
    ...,
]:
    rows = (
        await db.execute(
            select(
                PurchaseValueCorrectionAllocationEvent
            )
            .join(
                InvoiceFulfillmentAllocation,
                and_(
                    InvoiceFulfillmentAllocation.company_id
                    == (
                        PurchaseValueCorrectionAllocationEvent
                        .company_id
                    ),
                    InvoiceFulfillmentAllocation.id
                    == (
                        PurchaseValueCorrectionAllocationEvent
                        .invoice_fulfillment_allocation_id
                    ),
                ),
            )
            .where(
                PurchaseValueCorrectionAllocationEvent.company_id
                == company_id,
                InvoiceFulfillmentAllocation.fulfillment_line_id
                == fulfillment_line_id,
            )
            .order_by(
                PurchaseValueCorrectionAllocationEvent.id
            )
            .with_for_update(
                of=PurchaseValueCorrectionAllocationEvent
            )
        )
    ).scalars().all()

    return tuple(
        rows
    )


def _resolve_active_pvca_originals(
    events: tuple[
        PurchaseValueCorrectionAllocationEvent,
        ...,
    ],
) -> tuple[
    PurchaseValueCorrectionAllocationEvent,
    ...,
]:
    by_id = {}

    for event in events:
        event_id = _positive_id(
            event.id,
            field="PVCA event id",
        )

        if event_id in by_id:
            raise (
                PurchaseValueCorrectionFifoImpactReconciliationDataIntegrityError(
                    "Duplicate PVCA event id"
                )
            )

        by_id[
            event_id
        ] = event

    reversed_ids = set()

    for event in events:
        if event.reversal_of_id is None:
            continue

        original = by_id.get(
            event.reversal_of_id
        )

        if original is None:
            raise (
                PurchaseValueCorrectionFifoImpactReconciliationDataIntegrityError(
                    "PVCA reversal references event "
                    "outside fulfillment-line history"
                )
            )

        if original.reversal_of_id is not None:
            raise (
                PurchaseValueCorrectionFifoImpactReconciliationDataIntegrityError(
                    "PVCA reversal cannot reverse "
                    "another reversal"
                )
            )

        if (
            event.reversal_of_id
            in reversed_ids
        ):
            raise (
                PurchaseValueCorrectionFifoImpactReconciliationDataIntegrityError(
                    "PVCA original has multiple reversals"
                )
            )

        if (
            event.trade_value_correction_event_id
            != original.trade_value_correction_event_id
            or (
                event.invoice_fulfillment_allocation_id
                != original.invoice_fulfillment_allocation_id
            )
        ):
            raise (
                PurchaseValueCorrectionFifoImpactReconciliationDataIntegrityError(
                    "PVCA reversal changed source provenance"
                )
            )

        if (
            _decimal(
                event.original_allocated_base_amount,
                field="PVCA reversal original base",
            )
            != _decimal(
                original.original_allocated_base_amount,
                field="PVCA original original base",
            )
            or _decimal(
                event.corrected_allocated_base_amount,
                field="PVCA reversal corrected base",
            )
            != _decimal(
                original.corrected_allocated_base_amount,
                field="PVCA original corrected base",
            )
            or event.currency_code
            != original.currency_code
        ):
            raise (
                PurchaseValueCorrectionFifoImpactReconciliationDataIntegrityError(
                    "PVCA reversal changed immutable "
                    "economic snapshot"
                )
            )

        if (
            event.recognition_date
            < original.recognition_date
        ):
            raise (
                PurchaseValueCorrectionFifoImpactReconciliationChronologyError(
                    "PVCA reversal predates original"
                )
            )

        reversed_ids.add(
            event.reversal_of_id
        )

    active = tuple(
        event
        for event in events
        if (
            event.reversal_of_id is None
            and event.id not in reversed_ids
        )
    )

    grouped = {}

    for event in active:
        key = (
            event.trade_value_correction_event_id,
            event.invoice_fulfillment_allocation_id,
        )

        grouped.setdefault(
            key,
            [],
        ).append(
            event
        )

    for key, rows in grouped.items():
        if len(
            rows
        ) > 1:
            raise (
                PurchaseValueCorrectionFifoImpactReconciliationDataIntegrityError(
                    "PVCA source has multiple active originals: "
                    f"{key}"
                )
            )

    return tuple(
        sorted(
            active,
            key=lambda event: (
                event.recognition_date,
                event.invoice_fulfillment_allocation_id,
                event.id,
            ),
        )
    )


async def _load_active_consumption_candidates_for_update(
    db: AsyncSession,
    *,
    company_id: int,
    stock_lot_id: int,
) -> tuple[
    ActiveFifoConsumptionCandidate,
    ...,
]:
    consumptions = (
        await db.execute(
            select(
                StockLotConsumption
            )
            .where(
                StockLotConsumption.company_id
                == company_id,
                StockLotConsumption.stock_lot_id
                == stock_lot_id,
            )
            .order_by(
                StockLotConsumption.id
            )
            .with_for_update()
        )
    ).scalars().all()

    if not consumptions:
        return ()

    document_ids = tuple(
        sorted(
            {
                row.issue_document_id
                for row in consumptions
            }
        )
    )

    documents = (
        await db.execute(
            select(
                Document
            )
            .where(
                Document.company_id
                == company_id,
                Document.id.in_(
                    document_ids
                ),
            )
            .order_by(
                Document.id
            )
            .with_for_update()
        )
    ).scalars().all()

    document_by_id = {
        document.id: document
        for document in documents
    }

    if len(
        document_by_id
    ) != len(
        document_ids
    ):
        raise (
            PurchaseValueCorrectionFifoImpactReconciliationDataIntegrityError(
                "StockLotConsumption references "
                "missing ISSUE document"
            )
        )

    active = []

    for consumption in consumptions:
        document = document_by_id[
            consumption.issue_document_id
        ]

        if (
            document.document_type
            != DocumentType.ISSUE
        ):
            raise (
                PurchaseValueCorrectionFifoImpactReconciliationDataIntegrityError(
                    "StockLotConsumption references "
                    "a non-ISSUE document"
                )
            )

        if (
            document.status
            != DocumentStatus.POSTED
        ):
            continue

        active.append(
            ActiveFifoConsumptionCandidate(
                stock_lot_consumption_id=(
                    consumption.id
                ),
                issue_document_id=(
                    consumption.issue_document_id
                ),
                issue_document_line_id=(
                    consumption.issue_document_line_id
                ),
                issue_event_date=(
                    document.document_date
                ),
                quantity=_decimal(
                    consumption.quantity,
                    field="StockLotConsumption quantity",
                ),
            )
        )

    return tuple(
        active
    )


async def _load_impact_history_for_update(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_line_id: int,
) -> tuple[
    PurchaseValueCorrectionFifoImpactEvent,
    ...,
]:
    rows = (
        await db.execute(
            select(
                PurchaseValueCorrectionFifoImpactEvent
            )
            .join(
                PurchaseValueCorrectionAllocationEvent,
                and_(
                    PurchaseValueCorrectionAllocationEvent.company_id
                    == (
                        PurchaseValueCorrectionFifoImpactEvent
                        .company_id
                    ),
                    PurchaseValueCorrectionAllocationEvent.id
                    == (
                        PurchaseValueCorrectionFifoImpactEvent
                        .purchase_value_correction_allocation_event_id
                    ),
                ),
            )
            .join(
                InvoiceFulfillmentAllocation,
                and_(
                    InvoiceFulfillmentAllocation.company_id
                    == (
                        PurchaseValueCorrectionAllocationEvent
                        .company_id
                    ),
                    InvoiceFulfillmentAllocation.id
                    == (
                        PurchaseValueCorrectionAllocationEvent
                        .invoice_fulfillment_allocation_id
                    ),
                ),
            )
            .where(
                PurchaseValueCorrectionFifoImpactEvent.company_id
                == company_id,
                InvoiceFulfillmentAllocation.fulfillment_line_id
                == fulfillment_line_id,
            )
            .order_by(
                PurchaseValueCorrectionFifoImpactEvent.id
            )
            .with_for_update(
                of=PurchaseValueCorrectionFifoImpactEvent
            )
        )
    ).scalars().all()

    return tuple(
        rows
    )


def _removal_target(
    *,
    current: PurchaseValueCorrectionFifoImpactEvent,
    allocation_event: PurchaseValueCorrectionAllocationEvent,
) -> PurchaseValueCorrectionFifoImpactTarget:
    return (
        PurchaseValueCorrectionFifoImpactTarget(
            purchase_value_correction_allocation_event_id=(
                current
                .purchase_value_correction_allocation_event_id
            ),
            invoice_fulfillment_allocation_id=(
                allocation_event
                .invoice_fulfillment_allocation_id
            ),
            stock_lot_id=current.stock_lot_id,
            destination_kind=current.destination_kind,
            stock_lot_consumption_id=(
                current.stock_lot_consumption_id
            ),
            issue_document_id=(
                current.issue_document_id
            ),
            issue_document_line_id=(
                current.issue_document_line_id
            ),
            quantity=_decimal(
                current.quantity,
                field="current impact quantity",
            ),
            recognition_date=current.recognition_date,
            original_base_amount=_decimal(
                current.original_base_amount,
                field="current original base",
            ),
            corrected_base_amount=_decimal(
                current.original_base_amount,
                field="current original base",
            ),
            currency_code=current.currency_code,
        )
    )


async def _preload_fifo_transfer_router(
    db: AsyncSession,
    *,
    company_id: int,
    active_consumptions: tuple[
        ActiveFifoConsumptionCandidate,
        ...,
    ],
) -> PurchaseValueCorrectionFifoTransferRouter:
    """
    DB-aware preload boundary for FIFO Transfer↔PVC.

    The returned router is a synchronous immutable
    in-memory snapshot used by the pure calculator.
    """

    return (
        await preload_purchase_value_correction_fifo_transfer_router(
            db,
            company_id=company_id,
            active_consumptions=active_consumptions,
        )
    )


def _calculate_fifo_targets_with_transfer_router(
    *,
    stock_lot_id: int,
    receipt_quantity: Decimal,
    current_consumed_quantity: Decimal,
    active_allocation_peers: tuple[
        ActiveFifoAllocationPeerCandidate,
        ...,
    ],
    allocation_candidates: tuple[
        PurchaseValueCorrectionFifoAllocationCandidate,
        ...,
    ],
    active_consumptions: tuple[
        ActiveFifoConsumptionCandidate,
        ...,
    ],
    transfer_router: (
        PurchaseValueCorrectionFifoTransferRouter
        | None
    ),
) -> tuple[
    PurchaseValueCorrectionFifoImpactTarget,
    ...,
]:
    """
    Single pure-calculation wiring boundary.

    No SQL occurs here.
    """

    return (
        build_purchase_value_correction_fifo_impact_targets(
            stock_lot_id=stock_lot_id,
            receipt_quantity=receipt_quantity,
            current_consumed_quantity=(
                current_consumed_quantity
            ),
            active_allocation_peers=(
                active_allocation_peers
            ),
            allocation_candidates=(
                allocation_candidates
            ),
            active_consumptions=(
                active_consumptions
            ),
            transfer_destination_router=(
                transfer_router.route
                if transfer_router is not None
                else None
            ),
        )
    )


async def reconcile_purchase_value_correction_fifo_impacts_for_fulfillment_line(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_line_id: int,
    adjustment_date: date,
    created_by: int,
) -> PurchaseValueCorrectionFifoImpactReconciliationResult:
    """
    Reconcile all FIFO Purchase Value Correction impacts for one
    physical receipt fulfillment line.

    All ACTIVE IFA peers define physical StockLot intervals.

    Each active PVCA original is calculated independently, so
    multiple corrections may coexist for the same IFA without
    collapsing monetary sources.

    Current FIFO consumption truth:
        StockLot.original_quantity
        - StockLot.remaining_quantity

    Only StockLotConsumption rows whose ISSUE document is still
    POSTED are supplied as economically active consumptions.

    Forward-only chronology:

        brand-new correction source:
            pure primary recognition_date.

        unchanged active destination:
            preserve persisted recognition_date; no date churn.

        changed active destination:
            reversal + replacement on adjustment_date.

        disappeared destination:
            reversal on adjustment_date.

        new destination for a correction source that already has
        impact history:
            adjustment_date, because it is a later topology
            reclassification rather than a new primary source.

    Removed destinations are reconciled first.

    Caller owns COMMIT / ROLLBACK.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )

    fulfillment_line_id = _positive_id(
        fulfillment_line_id,
        field="fulfillment_line_id",
    )

    created_by = _positive_id(
        created_by,
        field="created_by",
    )

    adjustment_date = _business_date(
        adjustment_date,
        field="adjustment_date",
    )

    fulfillment_line, receipt = (
        await _lock_fulfillment_line_and_receipt(
            db,
            company_id=company_id,
            fulfillment_line_id=fulfillment_line_id,
        )
    )

    stock_lot = await _lock_stock_lot(
        db,
        company_id=company_id,
        fulfillment_line=fulfillment_line,
    )

    active_ifas = (
        await _load_active_ifa_peers_for_update(
            db,
            company_id=company_id,
            fulfillment_line_id=fulfillment_line_id,
        )
    )

    active_ifas_by_id = {
        row.id: row
        for row in active_ifas
    }

    pvca_history = (
        await _load_pvca_history_for_update(
            db,
            company_id=company_id,
            fulfillment_line_id=fulfillment_line_id,
        )
    )

    pvca_by_id = {
        event.id: event
        for event in pvca_history
    }

    active_pvca = tuple(
        event
        for event in _resolve_active_pvca_originals(
            pvca_history
        )
        if (
            event.invoice_fulfillment_allocation_id
            in active_ifas_by_id
        )
    )

    consumptions = (
        await _load_active_consumption_candidates_for_update(
            db,
            company_id=company_id,
            stock_lot_id=stock_lot.id,
        )
    )

    transfer_router = (
        await _preload_fifo_transfer_router(
            db,
            company_id=company_id,
            active_consumptions=consumptions,
        )
    )

    impact_history = (
        await _load_impact_history_for_update(
            db,
            company_id=company_id,
            fulfillment_line_id=fulfillment_line_id,
        )
    )

    _validate_impact_history(
        impact_history
    )

    active_impacts = (
        _active_impact_originals(
            impact_history
        )
    )

    active_impact_by_key = {}

    for event in active_impacts:
        key = _event_key(
            event
        )

        if key in active_impact_by_key:
            raise (
                PurchaseValueCorrectionFifoImpactReconciliationDataIntegrityError(
                    "Multiple active FIFO impact originals "
                    "exist for one destination key"
                )
            )

        active_impact_by_key[
            key
        ] = event

    peer_candidates = tuple(
        ActiveFifoAllocationPeerCandidate(
            invoice_fulfillment_allocation_id=(
                row.id
            ),
            receipt_event_date=(
                receipt.document_date
            ),
            quantity=_decimal(
                row.quantity,
                field="IFA quantity",
            ),
        )
        for row in active_ifas
    )

    receipt_quantity = _decimal(
        stock_lot.original_quantity,
        field="StockLot original_quantity",
    )

    remaining_quantity = _decimal(
        stock_lot.remaining_quantity,
        field="StockLot remaining_quantity",
    )

    current_consumed_quantity = (
        receipt_quantity
        - remaining_quantity
    )

    desired = []

    for source in active_pvca:
        ifa = active_ifas_by_id[
            source.invoice_fulfillment_allocation_id
        ]

        source_targets = (
            _calculate_fifo_targets_with_transfer_router(
                stock_lot_id=stock_lot.id,
                receipt_quantity=receipt_quantity,
                current_consumed_quantity=(
                    current_consumed_quantity
                ),
                active_allocation_peers=peer_candidates,
                allocation_candidates=(
                    PurchaseValueCorrectionFifoAllocationCandidate(
                        purchase_value_correction_allocation_event_id=(
                            source.id
                        ),
                        invoice_fulfillment_allocation_id=(
                            ifa.id
                        ),
                        recognition_date=(
                            source.recognition_date
                        ),
                        quantity=_decimal(
                            ifa.quantity,
                            field="IFA quantity",
                        ),
                        original_allocated_base_amount=_decimal(
                            source.original_allocated_base_amount,
                            field="PVCA original base",
                        ),
                        corrected_allocated_base_amount=_decimal(
                            source.corrected_allocated_base_amount,
                            field="PVCA corrected base",
                        ),
                        currency_code=(
                            source.currency_code
                        ),
                    ),
                ),
                active_consumptions=consumptions,
                transfer_router=transfer_router,
            )
        )

        desired.extend(
            source_targets
        )

    desired = tuple(
        sorted(
            desired,
            key=_target_key,
        )
    )

    desired_by_key = {}

    for target in desired:
        key = _target_key(
            target
        )

        if key in desired_by_key:
            raise (
                PurchaseValueCorrectionFifoImpactReconciliationDataIntegrityError(
                    "Pure FIFO calculation produced "
                    "duplicate destination key"
                )
            )

        desired_by_key[
            key
        ] = target

    history_source_ids = {
        event.purchase_value_correction_allocation_event_id
        for event in impact_history
    }

    effective_desired = []

    for target in desired:
        key = _target_key(
            target
        )

        current = active_impact_by_key.get(
            key
        )

        if (
            current is not None
            and not _target_is_noop(
                target
            )
            and _same_state_except_date(
                event=current,
                target=target,
            )
        ):
            effective_desired.append(
                replace(
                    target,
                    recognition_date=(
                        current.recognition_date
                    ),
                )
            )
            continue

        if (
            current is None
            and not _target_is_noop(
                target
            )
            and (
                target
                .purchase_value_correction_allocation_event_id
                in history_source_ids
            )
        ):
            if (
                adjustment_date
                < target.recognition_date
            ):
                raise (
                    PurchaseValueCorrectionFifoImpactReconciliationChronologyError(
                        "adjustment_date cannot predate "
                        "new topology destination "
                        "primary recognition_date"
                    )
                )

            effective_desired.append(
                replace(
                    target,
                    recognition_date=(
                        adjustment_date
                    ),
                )
            )
            continue

        effective_desired.append(
            target
        )

    effective_desired = tuple(
        effective_desired
    )

    effective_by_key = {
        _target_key(
            target
        ): target
        for target in effective_desired
    }

    removed = tuple(
        sorted(
            (
                event
                for key, event
                in active_impact_by_key.items()
                if key not in effective_by_key
            ),
            key=_event_key,
        )
    )

    for current in removed:
        if (
            adjustment_date
            < current.recognition_date
        ):
            raise (
                PurchaseValueCorrectionFifoImpactReconciliationChronologyError(
                    "adjustment_date cannot predate "
                    "removed active FIFO impact"
                )
            )

    for target in effective_desired:
        current = active_impact_by_key.get(
            _target_key(
                target
            )
        )

        if current is None:
            continue

        if (
            not _target_is_noop(
                target
            )
            and _same_state_except_date(
                event=current,
                target=target,
            )
            and (
                current.recognition_date
                == target.recognition_date
            )
        ):
            continue

        if (
            adjustment_date
            < current.recognition_date
        ):
            raise (
                PurchaseValueCorrectionFifoImpactReconciliationChronologyError(
                    "adjustment_date cannot predate "
                    "changed active FIFO impact"
                )
            )

        if (
            not _target_is_noop(
                target
            )
            and adjustment_date
            < target.recognition_date
        ):
            raise (
                PurchaseValueCorrectionFifoImpactReconciliationChronologyError(
                    "adjustment_date cannot predate "
                    "desired FIFO impact"
                )
            )

    created = []
    reconciliation_targets = []

    for current in removed:
        allocation_event = pvca_by_id.get(
            current
            .purchase_value_correction_allocation_event_id
        )

        if allocation_event is None:
            raise (
                PurchaseValueCorrectionFifoImpactReconciliationDataIntegrityError(
                    "Active FIFO impact references PVCA "
                    "outside fulfillment-line history"
                )
            )

        removal = _removal_target(
            current=current,
            allocation_event=allocation_event,
        )

        reconciliation_targets.append(
            removal
        )

        rows = (
            await reconcile_purchase_value_correction_fifo_impact_source(
                db,
                company_id=company_id,
                target=removal,
                created_by=created_by,
                reversal_date=adjustment_date,
            )
        )

        created.extend(
            rows
        )

    for target in effective_desired:
        reconciliation_targets.append(
            target
        )

        current = active_impact_by_key.get(
            _target_key(
                target
            )
        )

        reversal_date = (
            adjustment_date
            if current is not None
            else None
        )

        rows = (
            await reconcile_purchase_value_correction_fifo_impact_source(
                db,
                company_id=company_id,
                target=target,
                created_by=created_by,
                reversal_date=reversal_date,
            )
        )

        created.extend(
            rows
        )

    return (
        PurchaseValueCorrectionFifoImpactReconciliationResult(
            fulfillment_line_id=(
                fulfillment_line_id
            ),
            stock_lot_id=(
                stock_lot.id
            ),
            active_allocation_peer_ids=tuple(
                row.id
                for row in active_ifas
            ),
            active_correction_allocation_event_ids=tuple(
                event.id
                for event in active_pvca
            ),
            desired_targets=desired,
            reconciliation_targets=tuple(
                reconciliation_targets
            ),
            created_events=tuple(
                created
            ),
        )
    )
