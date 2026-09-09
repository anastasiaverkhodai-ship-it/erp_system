from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.moving_average_movement import (
    MovingAverageMovement,
)
from app.models.purchase_value_correction_allocation_event import (
    PurchaseValueCorrectionAllocationEvent,
)
from app.models.trade_fulfillment_line import (
    TradeFulfillmentLine,
)
from app.services.purchase_value_correction_moving_average_peer_replay_service import (
    MovingAveragePeerCorrection,
)
from app.services.purchase_value_correction_moving_average_replay_source_loader import (
    PurchaseValueCorrectionMovingAverageReplaySource,
    load_purchase_value_correction_moving_average_replay_source,
)


ZERO = Decimal("0")


class PurchaseValueCorrectionMovingAveragePeerSourceError(
    Exception
):
    pass


class PurchaseValueCorrectionMovingAveragePeerSourceIntegrityError(
    PurchaseValueCorrectionMovingAveragePeerSourceError
):
    pass


class PurchaseValueCorrectionMovingAveragePeerSourceNotFoundError(
    PurchaseValueCorrectionMovingAveragePeerSourceError
):
    pass


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionMovingAveragePeerSource:
    """
    Complete peer-aware input for one physical MA receipt.

    base_source:
        immutable MA chronology for anchor receipt.

    peers:
        every ACTIVE PVCA original whose IFA fulfillment line maps
        to the exact same warehouse receipt document + line.
    """

    base_source: PurchaseValueCorrectionMovingAverageReplaySource

    peers: tuple[
        MovingAveragePeerCorrection,
        ...,
    ]

    @property
    def company_id(self) -> int:
        return self.base_source.company_id

    @property
    def product_id(self) -> int:
        return self.base_source.product_id

    @property
    def warehouse_id(self) -> int:
        return self.base_source.warehouse_id

    @property
    def allocation_event_ids(
        self,
    ) -> tuple[int, ...]:
        return tuple(
            peer.allocation_event_id
            for peer in self.peers
        )


def _decimal(
    value,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise (
            PurchaseValueCorrectionMovingAveragePeerSourceIntegrityError(
                f"{field} must be Decimal-compatible"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseValueCorrectionMovingAveragePeerSourceIntegrityError(
                f"{field} must be finite"
            )
        )

    return result


def _positive_id(
    value,
    *,
    field: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise (
            PurchaseValueCorrectionMovingAveragePeerSourceIntegrityError(
                f"{field} must be a positive integer"
            )
        )

    return value


def _enum_value(value) -> str:
    return str(
        getattr(
            value,
            "value",
            value,
        )
    ).lower()


def _active_allocation_originals(
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
            field="PVCA id",
        )

        if event_id in by_id:
            raise (
                PurchaseValueCorrectionMovingAveragePeerSourceIntegrityError(
                    "Duplicate PVCA id"
                )
            )

        by_id[event_id] = event

    reversed_ids = set()

    for event in events:
        if event.reversal_of_id is None:
            continue

        original_id = _positive_id(
            event.reversal_of_id,
            field="PVCA reversal_of_id",
        )

        original = by_id.get(original_id)

        if original is None:
            raise (
                PurchaseValueCorrectionMovingAveragePeerSourceIntegrityError(
                    "PVCA reversal references unloaded original"
                )
            )

        if original.reversal_of_id is not None:
            raise (
                PurchaseValueCorrectionMovingAveragePeerSourceIntegrityError(
                    "PVCA reversal cannot reverse another reversal"
                )
            )

        if original_id in reversed_ids:
            raise (
                PurchaseValueCorrectionMovingAveragePeerSourceIntegrityError(
                    "PVCA original has multiple reversals"
                )
            )

        if event.company_id != original.company_id:
            raise (
                PurchaseValueCorrectionMovingAveragePeerSourceIntegrityError(
                    "PVCA reversal changed company provenance"
                )
            )

        reversed_ids.add(original_id)

    return tuple(
        event
        for event in events
        if (
            event.reversal_of_id is None
            and event.id not in reversed_ids
        )
    )


def _build_peer_source_from_rows(
    *,
    base_source: PurchaseValueCorrectionMovingAverageReplaySource,
    source_receipt_movement: MovingAverageMovement,
    allocation_events: tuple[
        PurchaseValueCorrectionAllocationEvent,
        ...,
    ],
    allocations_by_id: Mapping[
        int,
        InvoiceFulfillmentAllocation,
    ],
    fulfillment_lines_by_id: Mapping[
        int,
        TradeFulfillmentLine,
    ],
) -> PurchaseValueCorrectionMovingAveragePeerSource:
    active = _active_allocation_originals(
        allocation_events
    )

    source_document_id = _positive_id(
        source_receipt_movement.document_id,
        field="source receipt document_id",
    )

    source_document_line_id = _positive_id(
        source_receipt_movement.document_line_id,
        field="source receipt document_line_id",
    )

    if (
        source_receipt_movement.company_id
        != base_source.company_id
        or source_receipt_movement.product_id
        != base_source.product_id
        or source_receipt_movement.warehouse_id
        != base_source.warehouse_id
    ):
        raise (
            PurchaseValueCorrectionMovingAveragePeerSourceIntegrityError(
                "Source receipt MA movement stream does not "
                "match base replay source"
            )
        )

    peers = []

    for event in active:
        if event.company_id != base_source.company_id:
            continue

        allocation_id = _positive_id(
            event.invoice_fulfillment_allocation_id,
            field="PVCA IFA id",
        )

        allocation = allocations_by_id.get(
            allocation_id
        )

        if allocation is None:
            raise (
                PurchaseValueCorrectionMovingAveragePeerSourceNotFoundError(
                    "Active PVCA IFA was not loaded"
                )
            )

        if allocation.company_id != base_source.company_id:
            raise (
                PurchaseValueCorrectionMovingAveragePeerSourceIntegrityError(
                    "IFA company provenance mismatch"
                )
            )

        if _enum_value(allocation.status) != "active":
            continue

        line_id = _positive_id(
            allocation.fulfillment_line_id,
            field="IFA fulfillment_line_id",
        )

        line = fulfillment_lines_by_id.get(
            line_id
        )

        if line is None:
            raise (
                PurchaseValueCorrectionMovingAveragePeerSourceNotFoundError(
                    "Active PVCA fulfillment line was not loaded"
                )
            )

        if line.company_id != base_source.company_id:
            raise (
                PurchaseValueCorrectionMovingAveragePeerSourceIntegrityError(
                    "Fulfillment line company provenance mismatch"
                )
            )

        if (
            line.product_id != base_source.product_id
            or line.warehouse_id != base_source.warehouse_id
        ):
            continue

        if (
            line.warehouse_document_id != source_document_id
            or line.warehouse_document_line_id
            != source_document_line_id
        ):
            continue

        currency = str(
            event.currency_code
        ).upper()

        if currency != base_source.currency_code.upper():
            raise (
                PurchaseValueCorrectionMovingAveragePeerSourceIntegrityError(
                    "Peer PVCA currency differs from base correction"
                )
            )

        original = _decimal(
            event.original_allocated_base_amount,
            field="original_allocated_base_amount",
        )

        corrected = _decimal(
            event.corrected_allocated_base_amount,
            field="corrected_allocated_base_amount",
        )

        if original < ZERO or corrected < ZERO:
            raise (
                PurchaseValueCorrectionMovingAveragePeerSourceIntegrityError(
                    "PVCA allocated amounts cannot be negative"
                )
            )

        delta = corrected - original

        if delta == ZERO:
            raise (
                PurchaseValueCorrectionMovingAveragePeerSourceIntegrityError(
                    "Active peer PVCA cannot be a no-op"
                )
            )

        peers.append(
            MovingAveragePeerCorrection(
                allocation_event_id=_positive_id(
                    event.id,
                    field="PVCA id",
                ),
                recognition_date=event.recognition_date,
                allocation_value_delta=delta,
            )
        )

    if not peers:
        raise (
            PurchaseValueCorrectionMovingAveragePeerSourceNotFoundError(
                "No active PVC peers exist for source MA receipt"
            )
        )

    anchor_matches = [
        peer
        for peer in peers
        if (
            peer.allocation_event_id
            == (
                base_source
                .purchase_value_correction_allocation_event_id
            )
        )
    ]

    if len(anchor_matches) != 1:
        raise (
            PurchaseValueCorrectionMovingAveragePeerSourceIntegrityError(
                "Anchor PVCA is not exactly one active peer"
            )
        )

    ids = [
        peer.allocation_event_id
        for peer in peers
    ]

    if len(ids) != len(set(ids)):
        raise (
            PurchaseValueCorrectionMovingAveragePeerSourceIntegrityError(
                "Duplicate peer allocation event id"
            )
        )

    return PurchaseValueCorrectionMovingAveragePeerSource(
        base_source=base_source,
        peers=tuple(peers),
    )


async def load_purchase_value_correction_moving_average_peer_source(
    db: AsyncSession,
    *,
    company_id: int,
    allocation_event_id: int,
) -> PurchaseValueCorrectionMovingAveragePeerSource:
    """
    Load every active PVCA affecting the same physical MA receipt
    as allocation_event_id.

    No writes.
    No COMMIT.
    No ROLLBACK.
    Caller owns transaction.
    """

    base_source = (
        await load_purchase_value_correction_moving_average_replay_source(
            db,
            company_id=company_id,
            allocation_event_id=allocation_event_id,
        )
    )

    source_receipt = (
        await db.execute(
            select(
                MovingAverageMovement
            )
            .where(
                MovingAverageMovement.company_id
                == company_id,
                MovingAverageMovement.id
                == (
                    base_source
                    .source_receipt_moving_average_movement_id
                ),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if source_receipt is None:
        raise (
            PurchaseValueCorrectionMovingAveragePeerSourceNotFoundError(
                "Source MA receipt movement was not found"
            )
        )

    events = tuple(
        (
            await db.execute(
                select(
                    PurchaseValueCorrectionAllocationEvent
                )
                .where(
                    PurchaseValueCorrectionAllocationEvent.company_id
                    == company_id,
                )
                .order_by(
                    PurchaseValueCorrectionAllocationEvent.id
                )
                .with_for_update()
            )
        ).scalars().all()
    )

    if not events:
        raise (
            PurchaseValueCorrectionMovingAveragePeerSourceNotFoundError(
                "Company contains no PVCA history"
            )
        )

    active = _active_allocation_originals(
        events
    )

    allocation_ids = sorted(
        {
            event.invoice_fulfillment_allocation_event_id
            if hasattr(
                event,
                "invoice_fulfillment_allocation_event_id",
            )
            else event.invoice_fulfillment_allocation_id
            for event in active
            if (
                getattr(
                    event,
                    "invoice_fulfillment_allocation_id",
                    None,
                )
                is not None
            )
        }
    )

    allocations = ()

    if allocation_ids:
        allocations = tuple(
            (
                await db.execute(
                    select(
                        InvoiceFulfillmentAllocation
                    )
                    .where(
                        InvoiceFulfillmentAllocation.company_id
                        == company_id,
                        InvoiceFulfillmentAllocation.id.in_(
                            allocation_ids
                        ),
                    )
                    .order_by(
                        InvoiceFulfillmentAllocation.id
                    )
                    .with_for_update()
                )
            ).scalars().all()
        )

    allocations_by_id = {
        row.id: row
        for row in allocations
    }

    fulfillment_line_ids = sorted(
        {
            row.fulfillment_line_id
            for row in allocations
            if row.fulfillment_line_id is not None
        }
    )

    fulfillment_lines = ()

    if fulfillment_line_ids:
        fulfillment_lines = tuple(
            (
                await db.execute(
                    select(
                        TradeFulfillmentLine
                    )
                    .where(
                        TradeFulfillmentLine.company_id
                        == company_id,
                        TradeFulfillmentLine.id.in_(
                            fulfillment_line_ids
                        ),
                    )
                    .order_by(
                        TradeFulfillmentLine.id
                    )
                    .with_for_update()
                )
            ).scalars().all()
        )

    fulfillment_lines_by_id = {
        row.id: row
        for row in fulfillment_lines
    }

    return _build_peer_source_from_rows(
        base_source=base_source,
        source_receipt_movement=source_receipt,
        allocation_events=events,
        allocations_by_id=allocations_by_id,
        fulfillment_lines_by_id=(
            fulfillment_lines_by_id
        ),
    )
