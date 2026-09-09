from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import (
    Document,
    DocumentStatus,
    DocumentType,
)
from app.models.inventory_cost_entry import (
    InventoryCostEntry,
)
from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.moving_average_movement import (
    MovingAverageMovement,
)
from app.models.purchase_value_correction_allocation_event import (
    PurchaseValueCorrectionAllocationEvent,
)
from app.models.stock_ledger import (
    StockMovementType,
)
from app.models.trade_fulfillment_line import (
    TradeFulfillmentLine,
)
from app.services.purchase_value_correction_moving_average_replay_calculation_service import (
    MovingAverageReplayMovement,
)


ZERO = Decimal("0")


class PurchaseValueCorrectionMovingAverageReplaySourceError(
    Exception
):
    """Base MA replay source error."""


class PurchaseValueCorrectionMovingAverageReplaySourceNotFoundError(
    PurchaseValueCorrectionMovingAverageReplaySourceError
):
    """Required immutable source row was not found."""


class PurchaseValueCorrectionMovingAverageReplaySourceStateError(
    PurchaseValueCorrectionMovingAverageReplaySourceError
):
    """Source exists but is not economically active."""


class PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
    PurchaseValueCorrectionMovingAverageReplaySourceError
):
    """Persisted provenance/history is internally inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionMovingAverageReplaySource:
    """
    Complete deterministic input for one active PVC allocation
    against one historical moving-average receipt.

    Important monetary rule:

        PurchaseValueCorrectionAllocationEvent amounts describe
        only the economic amount allocated to one IFA.

        MovingAverageMovement.RECEIPT describes the complete
        physical warehouse receipt line.

    Therefore:

        original_receipt_value
            = historical MA RECEIPT.value_delta

        corrected_receipt_value
            = historical MA RECEIPT.value_delta
              + (
                    corrected_allocated_base_amount
                    - original_allocated_base_amount
                )

    The allocation amount itself must never replace the complete
    MA receipt value.
    """

    company_id: int

    purchase_value_correction_allocation_event_id: int
    invoice_fulfillment_allocation_id: int
    fulfillment_line_id: int

    source_receipt_moving_average_movement_id: int

    product_id: int
    warehouse_id: int

    recognition_date: date
    currency_code: str

    opening_quantity: Decimal
    opening_inventory_value: Decimal

    receipt_quantity: Decimal
    original_receipt_value: Decimal
    corrected_receipt_value: Decimal

    historical_receipt_balance_quantity_after: Decimal
    historical_receipt_balance_value_after: Decimal
    historical_receipt_average_unit_cost_after: Decimal

    later_movements: tuple[
        MovingAverageReplayMovement,
        ...,
    ]

    @property
    def allocation_value_delta(
        self,
    ) -> Decimal:
        return (
            self.corrected_receipt_value
            - self.original_receipt_value
        )


def _decimal(
    value,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(
            str(value)
        )
    except Exception as exc:
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                f"{field} must be Decimal-compatible"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
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
            PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                f"{field} must be a positive integer"
            )
        )

    return value


def _enum_value(
    value,
) -> str:
    raw = getattr(
        value,
        "value",
        value,
    )

    return str(
        raw
    ).lower()


def _validate_active_original_graph(
    movements: Iterable[
        MovingAverageMovement
    ],
) -> tuple[
    MovingAverageMovement,
    ...,
]:
    """
    Reconstruct active MA originals from immutable
    original/reversal movement history.

    Fail closed on:

    - missing / duplicate IDs
    - reversal referencing unloaded original
    - reversal of reversal
    - multiple reversals of one original
    - non-REVERSAL row carrying reversal_of_id
    - REVERSAL row missing reversal_of_id

    Result preserves MovingAverageMovement.id chronology.
    """

    values = tuple(
        movements
    )

    by_id = {}

    for movement in values:
        movement_id = _positive_id(
            movement.id,
            field="MovingAverageMovement.id",
        )

        if movement_id in by_id:
            raise (
                PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                    "Duplicate MovingAverageMovement.id"
                )
            )

        by_id[
            movement_id
        ] = movement

    reversed_ids = set()

    for movement in values:
        movement_type = _enum_value(
            movement.movement_type
        )

        if movement.reversal_of_id is None:
            if (
                movement_type
                == StockMovementType.REVERSAL.value
            ):
                raise (
                    PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                        "MA REVERSAL movement has no reversal_of_id"
                    )
                )

            continue

        if (
            movement_type
            != StockMovementType.REVERSAL.value
        ):
            raise (
                PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                    "Non-REVERSAL MA movement carries reversal_of_id"
                )
            )

        original_id = _positive_id(
            movement.reversal_of_id,
            field=(
                "MovingAverageMovement.reversal_of_id"
            ),
        )

        original = by_id.get(
            original_id
        )

        if original is None:
            raise (
                PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                    "MA reversal references an unloaded original"
                )
            )

        if original.reversal_of_id is not None:
            raise (
                PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                    "MA reversal cannot reverse another reversal"
                )
            )

        if (
            _enum_value(
                original.movement_type
            )
            == StockMovementType.REVERSAL.value
        ):
            raise (
                PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                    "MA reversal cannot reverse REVERSAL movement"
                )
            )

        if original_id in reversed_ids:
            raise (
                PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                    "MA original has multiple reversals"
                )
            )

        if (
            movement.company_id
            != original.company_id
            or movement.product_id
            != original.product_id
            or movement.warehouse_id
            != original.warehouse_id
        ):
            raise (
                PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                    "MA reversal changed stream provenance"
                )
            )

        reversed_ids.add(
            original_id
        )

    active = tuple(
        movement
        for movement in values
        if (
            movement.reversal_of_id is None
            and movement.id
            not in reversed_ids
        )
    )

    return tuple(
        sorted(
            active,
            key=lambda movement: movement.id,
        )
    )


def _build_replay_source_from_rows(
    *,
    company_id: int,
    allocation_event: PurchaseValueCorrectionAllocationEvent,
    allocation: InvoiceFulfillmentAllocation,
    fulfillment_line: TradeFulfillmentLine,
    receipt_document: Document,
    stream_movements: tuple[
        MovingAverageMovement,
        ...,
    ],
    inventory_cost_entries_by_document_line_id: Mapping[
        int,
        InventoryCostEntry,
    ],
) -> PurchaseValueCorrectionMovingAverageReplaySource:
    """
    Pure row-to-replay-source assembler.

    This function performs no database writes and is deliberately
    independently testable.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )

    allocation_event_id = _positive_id(
        allocation_event.id,
        field="PVCA id",
    )

    allocation_id = _positive_id(
        allocation.id,
        field="IFA id",
    )

    fulfillment_line_id = _positive_id(
        fulfillment_line.id,
        field="TradeFulfillmentLine id",
    )

    if (
        allocation_event.company_id
        != company_id
        or allocation.company_id
        != company_id
        or fulfillment_line.company_id
        != company_id
        or receipt_document.company_id
        != company_id
    ):
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                "PVC MA source company provenance mismatch"
            )
        )

    if allocation_event.reversal_of_id is not None:
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceStateError(
                "PVCA reversal cannot be a positive MA replay source"
            )
        )

    if (
        allocation_event.invoice_fulfillment_allocation_id
        != allocation_id
    ):
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                "PVCA does not reference supplied IFA"
            )
        )

    if (
        allocation.fulfillment_line_id
        != fulfillment_line_id
    ):
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                "IFA does not reference supplied fulfillment line"
            )
        )

    if (
        allocation.product_id
        != fulfillment_line.product_id
    ):
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                "IFA product does not match fulfillment line"
            )
        )

    if (
        receipt_document.id
        != fulfillment_line.warehouse_document_id
    ):
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                "Receipt document does not match fulfillment line"
            )
        )

    if (
        _enum_value(
            receipt_document.document_type
        )
        != DocumentType.RECEIPT.value
        or _enum_value(
            receipt_document.status
        )
        != DocumentStatus.POSTED.value
    ):
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceStateError(
                "Fulfillment source is not a POSTED RECEIPT"
            )
        )

    original_allocated = _decimal(
        allocation_event.original_allocated_base_amount,
        field=(
            "PVCA original_allocated_base_amount"
        ),
    )
    corrected_allocated = _decimal(
        allocation_event.corrected_allocated_base_amount,
        field=(
            "PVCA corrected_allocated_base_amount"
        ),
    )

    if (
        original_allocated < ZERO
        or corrected_allocated < ZERO
    ):
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                "PVCA allocated amounts cannot be negative"
            )
        )

    allocation_delta = (
        corrected_allocated
        - original_allocated
    )

    if allocation_delta == ZERO:
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceStateError(
                "Active PVCA MA replay source cannot be a no-op"
            )
        )

    active_movements = (
        _validate_active_original_graph(
            stream_movements
        )
    )

    for movement in active_movements:
        if (
            movement.company_id
            != company_id
            or movement.product_id
            != fulfillment_line.product_id
            or movement.warehouse_id
            != fulfillment_line.warehouse_id
        ):
            raise (
                PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                    "Loaded MA movement belongs to another stream"
                )
            )

    source_candidates = tuple(
        movement
        for movement in active_movements
        if (
            movement.document_id
            == fulfillment_line.warehouse_document_id
            and movement.document_line_id
            == fulfillment_line.warehouse_document_line_id
            and _enum_value(
                movement.movement_type
            )
            == StockMovementType.RECEIPT.value
        )
    )

    if not source_candidates:
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceNotFoundError(
                "MA receipt movement for fulfillment line was not found"
            )
        )

    if len(
        source_candidates
    ) != 1:
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                "Multiple active MA receipt movements exist "
                "for one fulfillment receipt line"
            )
        )

    source_receipt = (
        source_candidates[
            0
        ]
    )

    if (
        source_receipt.movement_date
        != receipt_document.document_date
    ):
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                "MA receipt date does not match receipt document"
            )
        )

    receipt_quantity = _decimal(
        source_receipt.quantity_delta,
        field="MA receipt quantity_delta",
    )

    original_receipt_value = _decimal(
        source_receipt.value_delta,
        field="MA receipt value_delta",
    )

    if receipt_quantity <= ZERO:
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                "MA source receipt quantity must be positive"
            )
        )

    if original_receipt_value < ZERO:
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                "MA source receipt value cannot be negative"
            )
        )

    fulfillment_quantity = _decimal(
        fulfillment_line.quantity,
        field="TradeFulfillmentLine quantity",
    )

    if receipt_quantity != fulfillment_quantity:
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                "MA receipt quantity does not match fulfillment line quantity"
            )
        )

    corrected_receipt_value = (
        original_receipt_value
        + allocation_delta
    )

    if corrected_receipt_value < ZERO:
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                "PVC allocation delta would make complete "
                "MA receipt value negative"
            )
        )

    preceding = tuple(
        movement
        for movement in active_movements
        if movement.id < source_receipt.id
    )

    if preceding:
        previous = preceding[
            -1
        ]

        opening_quantity = _decimal(
            previous.balance_quantity_after,
            field=(
                "previous MA balance_quantity_after"
            ),
        )
        opening_inventory_value = _decimal(
            previous.balance_value_after,
            field=(
                "previous MA balance_value_after"
            ),
        )
    else:
        opening_quantity = ZERO
        opening_inventory_value = ZERO

    later = tuple(
        movement
        for movement in active_movements
        if movement.id > source_receipt.id
    )

    replay_movements = []

    for movement in later:
        movement_type_value = _enum_value(
            movement.movement_type
        )

        try:
            movement_type = StockMovementType(
                movement_type_value
            )
        except ValueError as exc:
            raise (
                PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                    "Unsupported persisted MA movement type"
                )
            ) from exc

        inventory_cost_entry_id = None

        if (
            movement_type
            == StockMovementType.ISSUE
        ):
            cost_entry = (
                inventory_cost_entries_by_document_line_id.get(
                    movement.document_line_id
                )
            )

            if cost_entry is None:
                raise (
                    PurchaseValueCorrectionMovingAverageReplaySourceNotFoundError(
                        "Active MA ISSUE has no InventoryCostEntry"
                    )
                )

            if (
                cost_entry.company_id
                != company_id
                or cost_entry.document_id
                != movement.document_id
                or cost_entry.document_line_id
                != movement.document_line_id
            ):
                raise (
                    PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                        "InventoryCostEntry provenance does not "
                        "match MA ISSUE movement"
                    )
                )

            valuation_method = _enum_value(
                cost_entry.valuation_method
            )

            if (
                valuation_method
                != "weighted_average_moving"
            ):
                raise (
                    PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                        "MA ISSUE InventoryCostEntry has "
                        "wrong valuation method"
                    )
                )

            inventory_cost_entry_id = _positive_id(
                cost_entry.id,
                field="InventoryCostEntry.id",
            )

        replay_movements.append(
            MovingAverageReplayMovement(
                movement_id=_positive_id(
                    movement.id,
                    field=(
                        "MovingAverageMovement.id"
                    ),
                ),
                movement_date=(
                    movement.movement_date
                ),
                movement_type=movement_type,
                quantity_delta=_decimal(
                    movement.quantity_delta,
                    field=(
                        "MA quantity_delta"
                    ),
                ),
                value_delta=_decimal(
                    movement.value_delta,
                    field=(
                        "MA value_delta"
                    ),
                ),
                balance_quantity_after=_decimal(
                    movement.balance_quantity_after,
                    field=(
                        "MA balance_quantity_after"
                    ),
                ),
                balance_value_after=_decimal(
                    movement.balance_value_after,
                    field=(
                        "MA balance_value_after"
                    ),
                ),
                average_unit_cost_after=_decimal(
                    movement.average_unit_cost_after,
                    field=(
                        "MA average_unit_cost_after"
                    ),
                ),
                inventory_cost_entry_id=(
                    inventory_cost_entry_id
                ),
            )
        )

    currency_code = str(
        allocation_event.currency_code
    ).upper()

    if len(
        currency_code
    ) != 3:
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                "PVCA currency_code must contain 3 characters"
            )
        )

    return (
        PurchaseValueCorrectionMovingAverageReplaySource(
            company_id=company_id,
            purchase_value_correction_allocation_event_id=(
                allocation_event_id
            ),
            invoice_fulfillment_allocation_id=(
                allocation_id
            ),
            fulfillment_line_id=(
                fulfillment_line_id
            ),
            source_receipt_moving_average_movement_id=(
                source_receipt.id
            ),
            product_id=(
                fulfillment_line.product_id
            ),
            warehouse_id=(
                fulfillment_line.warehouse_id
            ),
            recognition_date=(
                allocation_event.recognition_date
            ),
            currency_code=currency_code,
            opening_quantity=(
                opening_quantity
            ),
            opening_inventory_value=(
                opening_inventory_value
            ),
            receipt_quantity=(
                receipt_quantity
            ),
            original_receipt_value=(
                original_receipt_value
            ),
            corrected_receipt_value=(
                corrected_receipt_value
            ),
            historical_receipt_balance_quantity_after=(
                _decimal(
                    source_receipt.balance_quantity_after,
                    field=(
                        "receipt MA "
                        "balance_quantity_after"
                    ),
                )
            ),
            historical_receipt_balance_value_after=(
                _decimal(
                    source_receipt.balance_value_after,
                    field=(
                        "receipt MA "
                        "balance_value_after"
                    ),
                )
            ),
            historical_receipt_average_unit_cost_after=(
                _decimal(
                    source_receipt.average_unit_cost_after,
                    field=(
                        "receipt MA "
                        "average_unit_cost_after"
                    ),
                )
            ),
            later_movements=tuple(
                replay_movements
            ),
        )
    )


async def load_purchase_value_correction_moving_average_replay_source(
    db: AsyncSession,
    *,
    company_id: int,
    allocation_event_id: int,
) -> PurchaseValueCorrectionMovingAverageReplaySource:
    """
    Load and lock all persisted provenance required to replay
    one active PVC allocation against moving-average chronology.

    No COMMIT / ROLLBACK.
    Caller owns transaction.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )

    allocation_event_id = _positive_id(
        allocation_event_id,
        field="allocation_event_id",
    )

    allocation_event = (
        await db.execute(
            select(
                PurchaseValueCorrectionAllocationEvent
            )
            .where(
                PurchaseValueCorrectionAllocationEvent.company_id
                == company_id,
                PurchaseValueCorrectionAllocationEvent.id
                == allocation_event_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if allocation_event is None:
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceNotFoundError(
                "PurchaseValueCorrectionAllocationEvent "
                "was not found"
            )
        )

    if allocation_event.reversal_of_id is not None:
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceStateError(
                "PVCA reversal cannot be a positive "
                "MA replay source"
            )
        )

    reversing_id = (
        await db.execute(
            select(
                PurchaseValueCorrectionAllocationEvent.id
            )
            .where(
                PurchaseValueCorrectionAllocationEvent.company_id
                == company_id,
                PurchaseValueCorrectionAllocationEvent.reversal_of_id
                == allocation_event.id,
            )
        )
    ).scalar_one_or_none()

    if reversing_id is not None:
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceStateError(
                "PVCA source is no longer active"
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
            PurchaseValueCorrectionMovingAverageReplaySourceNotFoundError(
                "InvoiceFulfillmentAllocation was not found"
            )
        )

    if (
        _enum_value(
            allocation.status
        )
        != "active"
    ):
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceStateError(
                "InvoiceFulfillmentAllocation is not ACTIVE"
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
            PurchaseValueCorrectionMovingAverageReplaySourceNotFoundError(
                "TradeFulfillmentLine was not found"
            )
        )

    receipt_document = (
        await db.execute(
            select(
                Document
            )
            .where(
                Document.company_id
                == company_id,
                Document.id
                == fulfillment_line.warehouse_document_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if receipt_document is None:
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceNotFoundError(
                "Receipt Document was not found"
            )
        )

    stream_movements = tuple(
        (
            await db.execute(
                select(
                    MovingAverageMovement
                )
                .where(
                    MovingAverageMovement.company_id
                    == company_id,
                    MovingAverageMovement.product_id
                    == fulfillment_line.product_id,
                    MovingAverageMovement.warehouse_id
                    == fulfillment_line.warehouse_id,
                )
                .order_by(
                    MovingAverageMovement.id
                )
                .with_for_update()
            )
        ).scalars().all()
    )

    if not stream_movements:
        raise (
            PurchaseValueCorrectionMovingAverageReplaySourceNotFoundError(
                "Moving-average stream contains no movements"
            )
        )

    issue_document_line_ids = {
        movement.document_line_id
        for movement in stream_movements
        if (
            _enum_value(
                movement.movement_type
            )
            == StockMovementType.ISSUE.value
            and movement.document_line_id is not None
        )
    }

    cost_entries_by_line = {}

    if issue_document_line_ids:
        cost_entries = (
            await db.execute(
                select(
                    InventoryCostEntry
                )
                .where(
                    InventoryCostEntry.company_id
                    == company_id,
                    InventoryCostEntry.document_line_id.in_(
                        sorted(
                            issue_document_line_ids
                        )
                    ),
                )
                .order_by(
                    InventoryCostEntry.id
                )
                .with_for_update()
            )
        ).scalars().all()

        for cost_entry in cost_entries:
            line_id = (
                cost_entry.document_line_id
            )

            if line_id in cost_entries_by_line:
                raise (
                    PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError(
                        "Multiple InventoryCostEntry rows "
                        "exist for one document line"
                    )
                )

            cost_entries_by_line[
                line_id
            ] = cost_entry

    return _build_replay_source_from_rows(
        company_id=company_id,
        allocation_event=allocation_event,
        allocation=allocation,
        fulfillment_line=fulfillment_line,
        receipt_document=receipt_document,
        stream_movements=stream_movements,
        inventory_cost_entries_by_document_line_id=(
            cost_entries_by_line
        ),
    )
