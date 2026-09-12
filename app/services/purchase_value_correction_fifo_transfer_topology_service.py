from dataclasses import dataclass
from datetime import date
from decimal import (
    Decimal,
    InvalidOperation,
)

from sqlalchemy import (
    and_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import (
    Document,
    DocumentStatus,
)
from app.models.stock_lot import StockLot
from app.models.stock_lot_consumption import (
    StockLotConsumption,
)
from app.services.purchase_value_correction_fifo_transfer_routing_service import (
    PurchaseValueCorrectionFifoTransferRoute,
)


ZERO = Decimal("0")
QUANTITY_QUANTUM = Decimal("0.0001")


class PurchaseValueCorrectionFifoTransferTopologyError(
    Exception
):
    """Base FIFO destination-topology failure."""


class PurchaseValueCorrectionFifoTransferTopologyIntegrityError(
    PurchaseValueCorrectionFifoTransferTopologyError
):
    """Transfer destination FIFO topology is inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionFifoTransferIssuedSlice:
    """
    One economically active downstream consumption of
    the transferred destination FIFO lot.

    issue_event_date is required for PVC recognition
    chronology:

        max(
            source correction recognition_date,
            downstream ISSUE document_date,
        )
    """

    stock_lot_consumption_id: int
    issue_document_id: int
    issue_document_line_id: int
    issue_event_date: date

    quantity: Decimal
    unit_cost: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class _ActiveConsumptionWithIssueDate:
    id: int
    company_id: int
    stock_lot_id: int
    issue_document_id: int
    issue_document_line_id: int
    issue_event_date: date
    quantity: Decimal
    unit_cost: Decimal


def _consumption_with_issue_date(
    *,
    consumption: StockLotConsumption,
    issue_event_date: date,
) -> _ActiveConsumptionWithIssueDate:
    return _ActiveConsumptionWithIssueDate(
        id=consumption.id,
        company_id=consumption.company_id,
        stock_lot_id=consumption.stock_lot_id,
        issue_document_id=consumption.issue_document_id,
        issue_document_line_id=(
            consumption.issue_document_line_id
        ),
        issue_event_date=issue_event_date,
        quantity=consumption.quantity,
        unit_cost=consumption.unit_cost,
    )


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionFifoTransferDestinationTopology:
    """
    Immutable current topology projection of one
    transferred FIFO valuation layer.

    classification:
      on_hand
          transferred layer remains completely in
          destination inventory;

      mixed
          part remains on hand and part has been
          consumed by active downstream ISSUEs;

      issued
          no transferred quantity remains on hand.

    This object is read-only. Historical StockLot and
    StockLotConsumption rows remain untouched.
    """

    route: PurchaseValueCorrectionFifoTransferRoute

    destination_stock_lot_id: int

    original_quantity: Decimal
    on_hand_quantity: Decimal
    issued_quantity: Decimal

    classification: str

    issued_slices: tuple[
        PurchaseValueCorrectionFifoTransferIssuedSlice,
        ...,
    ]


def _positive_id(
    value,
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
            PurchaseValueCorrectionFifoTransferTopologyIntegrityError(
                f"{field} must be a positive integer"
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
            str(value)
        )
    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ) as exc:
        raise (
            PurchaseValueCorrectionFifoTransferTopologyIntegrityError(
                f"{field} must be Decimal-compatible"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseValueCorrectionFifoTransferTopologyIntegrityError(
                f"{field} must be finite"
            )
        )

    return result


def _business_date(
    value,
    *,
    field: str,
) -> date:
    if not isinstance(value, date):
        raise (
            PurchaseValueCorrectionFifoTransferTopologyIntegrityError(
                f"{field} must be a date"
            )
        )

    return value


def _quantity(
    value,
    *,
    field: str,
) -> Decimal:
    return _decimal(
        value,
        field=field,
    ).quantize(
        QUANTITY_QUANTUM
    )


def build_fifo_transfer_destination_topology(
    *,
    route: PurchaseValueCorrectionFifoTransferRoute,
    stock_lot: StockLot,
    active_consumptions: tuple[
        StockLotConsumption,
        ...,
    ],
) -> PurchaseValueCorrectionFifoTransferDestinationTopology:
    """
    Build the economic destination topology for one
    transferred FIFO layer from already-loaded immutable
    provenance.

    active_consumptions must contain only consumptions
    whose ISSUE document is still POSTED.
    """

    lot_id = _positive_id(
        stock_lot.id,
        field="destination StockLot.id",
    )

    if (
        _positive_id(
            stock_lot.company_id,
            field="StockLot.company_id",
        )
        != route.company_id
    ):
        raise (
            PurchaseValueCorrectionFifoTransferTopologyIntegrityError(
                "destination stock lot company mismatch"
            )
        )

    if (
        _positive_id(
            stock_lot.product_id,
            field="StockLot.product_id",
        )
        != route.product_id
    ):
        raise (
            PurchaseValueCorrectionFifoTransferTopologyIntegrityError(
                "destination stock lot product mismatch"
            )
        )

    if (
        _positive_id(
            stock_lot.warehouse_id,
            field="StockLot.warehouse_id",
        )
        != route.destination_warehouse_id
    ):
        raise (
            PurchaseValueCorrectionFifoTransferTopologyIntegrityError(
                "destination stock lot warehouse mismatch"
            )
        )

    if (
        _positive_id(
            stock_lot.source_document_id,
            field="StockLot.source_document_id",
        )
        != route.destination_receipt_document_id
        or _positive_id(
            stock_lot.source_document_line_id,
            field="StockLot.source_document_line_id",
        )
        != route.destination_receipt_document_line_id
    ):
        raise (
            PurchaseValueCorrectionFifoTransferTopologyIntegrityError(
                "destination StockLot does not match "
                "transfer receipt provenance"
            )
        )

    original_quantity = _quantity(
        stock_lot.original_quantity,
        field="StockLot.original_quantity",
    )

    route_quantity = _quantity(
        route.quantity,
        field="route.quantity",
    )

    if original_quantity != route_quantity:
        raise (
            PurchaseValueCorrectionFifoTransferTopologyIntegrityError(
                "destination StockLot original_quantity "
                "does not match transfer route quantity"
            )
        )

    if original_quantity <= ZERO:
        raise (
            PurchaseValueCorrectionFifoTransferTopologyIntegrityError(
                "destination StockLot original_quantity "
                "must be greater than zero"
            )
        )

    on_hand_quantity = _quantity(
        stock_lot.remaining_quantity,
        field="StockLot.remaining_quantity",
    )

    if (
        on_hand_quantity < ZERO
        or on_hand_quantity > original_quantity
    ):
        raise (
            PurchaseValueCorrectionFifoTransferTopologyIntegrityError(
                "destination StockLot remaining quantity "
                "is outside valid range"
            )
        )

    issued_slices: list[
        PurchaseValueCorrectionFifoTransferIssuedSlice
    ] = []

    consumption_ids: set[int] = set()

    for row in active_consumptions:
        consumption_id = _positive_id(
            row.id,
            field="StockLotConsumption.id",
        )

        if consumption_id in consumption_ids:
            raise (
                PurchaseValueCorrectionFifoTransferTopologyIntegrityError(
                    "duplicate active StockLotConsumption id"
                )
            )

        consumption_ids.add(
            consumption_id
        )

        if (
            _positive_id(
                row.company_id,
                field="StockLotConsumption.company_id",
            )
            != route.company_id
        ):
            raise (
                PurchaseValueCorrectionFifoTransferTopologyIntegrityError(
                    "downstream consumption company mismatch"
                )
            )

        if (
            _positive_id(
                row.stock_lot_id,
                field="StockLotConsumption.stock_lot_id",
            )
            != lot_id
        ):
            raise (
                PurchaseValueCorrectionFifoTransferTopologyIntegrityError(
                    "downstream consumption references "
                    "another stock lot"
                )
            )

        quantity = _quantity(
            row.quantity,
            field="StockLotConsumption.quantity",
        )

        if quantity <= ZERO:
            raise (
                PurchaseValueCorrectionFifoTransferTopologyIntegrityError(
                    "downstream consumption quantity "
                    "must be greater than zero"
                )
            )

        unit_cost = _decimal(
            row.unit_cost,
            field="StockLotConsumption.unit_cost",
        )

        if unit_cost < ZERO:
            raise (
                PurchaseValueCorrectionFifoTransferTopologyIntegrityError(
                    "downstream consumption unit_cost "
                    "must be nonnegative"
                )
            )

        issued_slices.append(
            PurchaseValueCorrectionFifoTransferIssuedSlice(
                stock_lot_consumption_id=(
                    consumption_id
                ),
                issue_document_id=_positive_id(
                    row.issue_document_id,
                    field=(
                        "StockLotConsumption."
                        "issue_document_id"
                    ),
                ),
                issue_document_line_id=_positive_id(
                    row.issue_document_line_id,
                    field=(
                        "StockLotConsumption."
                        "issue_document_line_id"
                    ),
                ),
                issue_event_date=_business_date(
                    row.issue_event_date,
                    field="downstream ISSUE event date",
                ),
                quantity=quantity,
                unit_cost=unit_cost,
            )
        )

    issued_slices.sort(
        key=lambda item: (
            item.stock_lot_consumption_id
        )
    )

    issued_quantity = sum(
        (
            item.quantity
            for item in issued_slices
        ),
        ZERO,
    ).quantize(
        QUANTITY_QUANTUM
    )

    if (
        on_hand_quantity
        + issued_quantity
        != original_quantity
    ):
        raise (
            PurchaseValueCorrectionFifoTransferTopologyIntegrityError(
                "destination FIFO quantity conservation "
                "failed"
            )
        )

    if issued_quantity == ZERO:
        classification = "on_hand"
    elif on_hand_quantity == ZERO:
        classification = "issued"
    else:
        classification = "mixed"

    return (
        PurchaseValueCorrectionFifoTransferDestinationTopology(
            route=route,
            destination_stock_lot_id=lot_id,
            original_quantity=original_quantity,
            on_hand_quantity=on_hand_quantity,
            issued_quantity=issued_quantity,
            classification=classification,
            issued_slices=tuple(
                issued_slices
            ),
        )
    )


async def load_fifo_transfer_destination_topology(
    db: AsyncSession,
    *,
    route: PurchaseValueCorrectionFifoTransferRoute,
) -> PurchaseValueCorrectionFifoTransferDestinationTopology:
    """
    Resolve one transfer destination receipt line to the
    exact FIFO StockLot and its economically active
    downstream ISSUE consumptions.

    Reversed ISSUE documents are intentionally excluded
    from active topology. Their immutable historical
    StockLotConsumption rows remain in storage.
    """

    lot_rows = tuple(
        (
            await db.execute(
                select(
                    StockLot
                )
                .where(
                    StockLot.company_id
                    == route.company_id,
                    StockLot.product_id
                    == route.product_id,
                    StockLot.warehouse_id
                    == route.destination_warehouse_id,
                    StockLot.source_document_id
                    == route.destination_receipt_document_id,
                    StockLot.source_document_line_id
                    == (
                        route
                        .destination_receipt_document_line_id
                    ),
                )
                .order_by(
                    StockLot.id
                )
            )
        ).scalars().all()
    )

    if len(lot_rows) != 1:
        raise (
            PurchaseValueCorrectionFifoTransferTopologyIntegrityError(
                "transfer destination receipt must map "
                "to exactly one FIFO StockLot"
            )
        )

    stock_lot = lot_rows[0]

    rows = tuple(
        (
            await db.execute(
                select(
                    StockLotConsumption,
                    Document.document_date,
                )
                .join(
                    Document,
                    and_(
                        Document.company_id
                        == StockLotConsumption.company_id,
                        Document.id
                        == (
                            StockLotConsumption
                            .issue_document_id
                        ),
                    ),
                )
                .where(
                    StockLotConsumption.company_id
                    == route.company_id,
                    StockLotConsumption.stock_lot_id
                    == stock_lot.id,
                    Document.status
                    == DocumentStatus.POSTED,
                )
                .order_by(
                    Document.document_date,
                    StockLotConsumption.issue_document_id,
                    StockLotConsumption.issue_document_line_id,
                    StockLotConsumption.id,
                )
            )
        ).all()
    )

    active_consumptions = tuple(
        _consumption_with_issue_date(
            consumption=row[0],
            issue_event_date=row[1],
        )
        for row in rows
    )

    return build_fifo_transfer_destination_topology(
        route=route,
        stock_lot=stock_lot,
        active_consumptions=active_consumptions,
    )
