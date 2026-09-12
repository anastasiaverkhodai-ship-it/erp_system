from dataclasses import dataclass
from datetime import date
from decimal import (
    Decimal,
    InvalidOperation,
)

from app.services.purchase_value_correction_fifo_transfer_topology_service import (
    PurchaseValueCorrectionFifoTransferDestinationTopology,
)


ZERO = Decimal("0")
QUANTITY_QUANTUM = Decimal("0.0001")


class PurchaseValueCorrectionFifoTransferSliceError(
    Exception
):
    """Base FIFO transfer destination-slice failure."""


class PurchaseValueCorrectionFifoTransferSliceIntegrityError(
    PurchaseValueCorrectionFifoTransferSliceError
):
    """Transfer interval or topology is inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionFifoTransferDestinationSlice:
    destination_kind: str

    stock_lot_id: int
    stock_lot_consumption_id: int | None

    issue_document_id: int | None
    issue_document_line_id: int | None

    quantity: Decimal
    recognition_date: date


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
            PurchaseValueCorrectionFifoTransferSliceIntegrityError(
                f"{field} must be Decimal-compatible"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseValueCorrectionFifoTransferSliceIntegrityError(
                f"{field} must be finite"
            )
        )

    return result.quantize(
        QUANTITY_QUANTUM
    )


def _intersection_quantity(
    *,
    left_start: Decimal,
    left_end: Decimal,
    right_start: Decimal,
    right_end: Decimal,
) -> Decimal:
    start = max(
        left_start,
        right_start,
    )

    end = min(
        left_end,
        right_end,
    )

    if end <= start:
        return ZERO.quantize(
            QUANTITY_QUANTUM
        )

    return (
        end - start
    ).quantize(
        QUANTITY_QUANTUM
    )


def build_fifo_transfer_destination_slices(
    *,
    topology: PurchaseValueCorrectionFifoTransferDestinationTopology,
    local_start: Decimal,
    local_end: Decimal,
    source_recognition_date: date,
) -> tuple[
    PurchaseValueCorrectionFifoTransferDestinationSlice,
    ...,
]:
    """
    Re-route one interval INSIDE the source transfer
    StockLotConsumption to the economically active
    destination FIFO topology.

    local interval coordinates are relative to the
    transferred WTVL layer itself:

        0 <= local_start < local_end <= route.quantity

    No monetary allocation happens here.
    """

    if not isinstance(
        source_recognition_date,
        date,
    ):
        raise (
            PurchaseValueCorrectionFifoTransferSliceIntegrityError(
                "source_recognition_date must be a date"
            )
        )

    start = _decimal(
        local_start,
        field="local interval start",
    )

    end = _decimal(
        local_end,
        field="local interval end",
    )

    route_quantity = _decimal(
        topology.route.quantity,
        field="route quantity",
    )

    if (
        start < ZERO
        or end <= start
        or end > route_quantity
    ):
        raise (
            PurchaseValueCorrectionFifoTransferSliceIntegrityError(
                "local transfer interval is outside "
                "valid route interval"
            )
        )

    destination_stock_lot_id = (
        topology.destination_stock_lot_id
    )

    if (
        not isinstance(
            destination_stock_lot_id,
            int,
        )
        or isinstance(
            destination_stock_lot_id,
            bool,
        )
        or destination_stock_lot_id <= 0
    ):
        raise (
            PurchaseValueCorrectionFifoTransferSliceIntegrityError(
                "destination stock lot id is invalid"
            )
        )

    slices: list[
        PurchaseValueCorrectionFifoTransferDestinationSlice
    ] = []

    cursor = ZERO.quantize(
        QUANTITY_QUANTUM
    )

    for issued in topology.issued_slices:
        issued_quantity = _decimal(
            issued.quantity,
            field="issued slice quantity",
        )

        issued_end = (
            cursor
            + issued_quantity
        ).quantize(
            QUANTITY_QUANTUM
        )

        quantity = _intersection_quantity(
            left_start=start,
            left_end=end,
            right_start=cursor,
            right_end=issued_end,
        )

        if quantity > ZERO:
            slices.append(
                PurchaseValueCorrectionFifoTransferDestinationSlice(
                    destination_kind="issued",
                    stock_lot_id=(
                        destination_stock_lot_id
                    ),
                    stock_lot_consumption_id=(
                        issued
                        .stock_lot_consumption_id
                    ),
                    issue_document_id=(
                        issued.issue_document_id
                    ),
                    issue_document_line_id=(
                        issued.issue_document_line_id
                    ),
                    quantity=quantity,
                    recognition_date=max(
                        source_recognition_date,
                        issued.issue_event_date,
                    ),
                )
            )

        cursor = issued_end

    on_hand_start = cursor
    on_hand_end = (
        cursor
        + _decimal(
            topology.on_hand_quantity,
            field="on_hand quantity",
        )
    ).quantize(
        QUANTITY_QUANTUM
    )

    if on_hand_end != route_quantity:
        raise (
            PurchaseValueCorrectionFifoTransferSliceIntegrityError(
                "destination topology does not conserve "
                "transfer route quantity"
            )
        )

    on_hand_quantity = _intersection_quantity(
        left_start=start,
        left_end=end,
        right_start=on_hand_start,
        right_end=on_hand_end,
    )

    if on_hand_quantity > ZERO:
        slices.append(
            PurchaseValueCorrectionFifoTransferDestinationSlice(
                destination_kind="on_hand",
                stock_lot_id=(
                    destination_stock_lot_id
                ),
                stock_lot_consumption_id=None,
                issue_document_id=None,
                issue_document_line_id=None,
                quantity=on_hand_quantity,
                recognition_date=(
                    source_recognition_date
                ),
            )
        )

    expected = (
        end - start
    ).quantize(
        QUANTITY_QUANTUM
    )

    actual = sum(
        (
            item.quantity
            for item in slices
        ),
        ZERO,
    ).quantize(
        QUANTITY_QUANTUM
    )

    if actual != expected:
        raise (
            PurchaseValueCorrectionFifoTransferSliceIntegrityError(
                "destination interval slice conservation "
                "failed"
            )
        )

    return tuple(
        slices
    )
