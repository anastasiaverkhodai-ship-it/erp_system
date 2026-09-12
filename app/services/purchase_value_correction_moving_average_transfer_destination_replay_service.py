from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import (
    purchase_value_correction_moving_average_replay_source_loader
    as replay_source_loader,
)
from app.services.purchase_value_correction_moving_average_replay_calculation_service import (
    MovingAverageReplayMovement,
    PurchaseValueCorrectionMovingAverageReplayResult,
    calculate_purchase_value_correction_moving_average_replay,
)
from app.services.purchase_value_correction_moving_average_transfer_routing_service import (
    PurchaseValueCorrectionMovingAverageTransferRoute,
)


ZERO = Decimal("0")


class PurchaseValueCorrectionMovingAverageTransferDestinationReplayError(
    Exception
):
    """Base destination MA replay composition error."""


class PurchaseValueCorrectionMovingAverageTransferDestinationReplayNotFoundError(
    PurchaseValueCorrectionMovingAverageTransferDestinationReplayError
):
    """Required immutable destination MA provenance was not found."""


class PurchaseValueCorrectionMovingAverageTransferDestinationReplayIntegrityError(
    PurchaseValueCorrectionMovingAverageTransferDestinationReplayError
):
    """Destination MA chronology/provenance is inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionMovingAverageTransferDestinationReplaySource:
    """
    Deterministic destination-side input for one routed MA correction.

    The transfer source ISSUE is not a final economic destination.

    Its valuation delta is injected into the exact immutable
    destination transfer RECEIPT and replayed through the EXISTING
    moving-average calculator.
    """

    warehouse_transfer_valuation_layer_id: int

    source_inventory_cost_entry_id: int
    destination_inventory_cost_entry_id: int

    company_id: int
    product_id: int
    warehouse_id: int

    destination_receipt_moving_average_movement_id: int

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
    def routed_valuation_delta(
        self,
    ) -> Decimal:
        return (
            self.corrected_receipt_value
            - self.original_receipt_value
        )


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionMovingAverageTransferDestinationReplay:
    source: (
        PurchaseValueCorrectionMovingAverageTransferDestinationReplaySource
    )
    replay_result: (
        PurchaseValueCorrectionMovingAverageReplayResult
    )


def _positive_id(
    value,
    *,
    field: str,
) -> int:
    if (
        not isinstance(
            value,
            int,
        )
        or isinstance(
            value,
            bool,
        )
        or value <= 0
    ):
        raise (
            PurchaseValueCorrectionMovingAverageTransferDestinationReplayIntegrityError(
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
            value
        )
    except Exception as exc:
        raise (
            PurchaseValueCorrectionMovingAverageTransferDestinationReplayIntegrityError(
                f"{field} must be a finite decimal"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseValueCorrectionMovingAverageTransferDestinationReplayIntegrityError(
                f"{field} must be finite"
            )
        )

    return result


def _movement_type(
    movement,
):
    try:
        return (
            replay_source_loader
            .StockMovementType(
                replay_source_loader._enum_value(
                    movement.movement_type
                )
            )
        )
    except ValueError as exc:
        raise (
            PurchaseValueCorrectionMovingAverageTransferDestinationReplayIntegrityError(
                "Unsupported persisted destination MA movement type"
            )
        ) from exc


def _build_replay_movement(
    *,
    company_id: int,
    movement,
    inventory_cost_entries_by_document_line_id: Mapping,
) -> MovingAverageReplayMovement:
    movement_type = _movement_type(
        movement
    )

    inventory_cost_entry_id = None

    if (
        movement_type
        == replay_source_loader.StockMovementType.ISSUE
    ):
        cost_entry = (
            inventory_cost_entries_by_document_line_id.get(
                movement.document_line_id
            )
        )

        if cost_entry is None:
            raise (
                PurchaseValueCorrectionMovingAverageTransferDestinationReplayNotFoundError(
                    "Active destination MA ISSUE has no "
                    "InventoryCostEntry"
                )
            )

        if (
            cost_entry.company_id != company_id
            or cost_entry.document_id
            != movement.document_id
            or cost_entry.document_line_id
            != movement.document_line_id
        ):
            raise (
                PurchaseValueCorrectionMovingAverageTransferDestinationReplayIntegrityError(
                    "Destination InventoryCostEntry provenance "
                    "does not match MA ISSUE movement"
                )
            )

        if (
            replay_source_loader._enum_value(
                cost_entry.valuation_method
            )
            != "weighted_average_moving"
        ):
            raise (
                PurchaseValueCorrectionMovingAverageTransferDestinationReplayIntegrityError(
                    "Destination MA ISSUE InventoryCostEntry "
                    "has wrong valuation method"
                )
            )

        inventory_cost_entry_id = _positive_id(
            cost_entry.id,
            field=(
                "destination InventoryCostEntry.id"
            ),
        )

    return MovingAverageReplayMovement(
        movement_id=_positive_id(
            movement.id,
            field=(
                "destination MovingAverageMovement.id"
            ),
        ),
        movement_date=movement.movement_date,
        movement_type=movement_type,
        quantity_delta=_decimal(
            movement.quantity_delta,
            field=(
                "destination MA quantity_delta"
            ),
        ),
        value_delta=_decimal(
            movement.value_delta,
            field=(
                "destination MA value_delta"
            ),
        ),
        balance_quantity_after=_decimal(
            movement.balance_quantity_after,
            field=(
                "destination MA balance_quantity_after"
            ),
        ),
        balance_value_after=_decimal(
            movement.balance_value_after,
            field=(
                "destination MA balance_value_after"
            ),
        ),
        average_unit_cost_after=_decimal(
            movement.average_unit_cost_after,
            field=(
                "destination MA average_unit_cost_after"
            ),
        ),
        inventory_cost_entry_id=(
            inventory_cost_entry_id
        ),
    )


def build_moving_average_transfer_destination_replay_source(
    *,
    route: PurchaseValueCorrectionMovingAverageTransferRoute,
    source_transfer_original_valuation_amount,
    source_transfer_corrected_valuation_amount,
    stream_movements: tuple,
    inventory_cost_entries_by_document_line_id: Mapping,
) -> (
    PurchaseValueCorrectionMovingAverageTransferDestinationReplaySource
):
    """
    Pure destination-side MA replay-source assembler.

    Important:
      * route is immutable WTVL provenance from B1;
      * source transfer ISSUE valuation delta is carried into the
        exact destination transfer RECEIPT;
      * source and destination warehouse streams are never merged;
      * historical rows are never mutated;
      * DocumentLine.price is not used.
    """

    company_id = _positive_id(
        route.company_id,
        field="route.company_id",
    )

    product_id = _positive_id(
        route.product_id,
        field="route.product_id",
    )

    warehouse_id = _positive_id(
        route.destination_warehouse_id,
        field="route.destination_warehouse_id",
    )

    original_transfer_issue_value = _decimal(
        source_transfer_original_valuation_amount,
        field=(
            "source transfer original valuation amount"
        ),
    )

    corrected_transfer_issue_value = _decimal(
        source_transfer_corrected_valuation_amount,
        field=(
            "source transfer corrected valuation amount"
        ),
    )

    if (
        original_transfer_issue_value < ZERO
        or corrected_transfer_issue_value < ZERO
    ):
        raise (
            PurchaseValueCorrectionMovingAverageTransferDestinationReplayIntegrityError(
                "Transfer valuation amounts cannot be negative"
            )
        )

    routed_delta = (
        corrected_transfer_issue_value
        - original_transfer_issue_value
    )

    if routed_delta == ZERO:
        raise (
            PurchaseValueCorrectionMovingAverageTransferDestinationReplayIntegrityError(
                "Transfer replay delta cannot be zero"
            )
        )

    active_movements = (
        replay_source_loader
        ._validate_active_original_graph(
            tuple(
                stream_movements
            )
        )
    )

    for movement in active_movements:
        if (
            movement.company_id != company_id
            or movement.product_id != product_id
            or movement.warehouse_id
            != warehouse_id
        ):
            raise (
                PurchaseValueCorrectionMovingAverageTransferDestinationReplayIntegrityError(
                    "Loaded destination MA movement belongs "
                    "to another stream"
                )
            )

    receipt_candidates = tuple(
        movement
        for movement in active_movements
        if (
            movement.document_id
            == route.destination_receipt_document_id
            and movement.document_line_id
            == route.destination_receipt_document_line_id
            and _movement_type(
                movement
            )
            == replay_source_loader.StockMovementType.RECEIPT
        )
    )

    if not receipt_candidates:
        raise (
            PurchaseValueCorrectionMovingAverageTransferDestinationReplayNotFoundError(
                "Destination transfer MA RECEIPT movement "
                "was not found"
            )
        )

    if len(
        receipt_candidates
    ) != 1:
        raise (
            PurchaseValueCorrectionMovingAverageTransferDestinationReplayIntegrityError(
                "Multiple active destination MA RECEIPT movements "
                "exist for one transfer receipt line"
            )
        )

    receipt = receipt_candidates[0]

    receipt_quantity = _decimal(
        receipt.quantity_delta,
        field=(
            "destination receipt MA quantity_delta"
        ),
    )

    receipt_value = _decimal(
        receipt.value_delta,
        field=(
            "destination receipt MA value_delta"
        ),
    )

    if receipt_quantity != route.quantity:
        raise (
            PurchaseValueCorrectionMovingAverageTransferDestinationReplayIntegrityError(
                "Destination MA receipt quantity does not match "
                "immutable WTVL quantity"
            )
        )

    if receipt_value != route.valuation_amount:
        raise (
            PurchaseValueCorrectionMovingAverageTransferDestinationReplayIntegrityError(
                "Destination MA receipt value does not match "
                "immutable WTVL Q8 valuation"
            )
        )

    if receipt_value != original_transfer_issue_value:
        raise (
            PurchaseValueCorrectionMovingAverageTransferDestinationReplayIntegrityError(
                "Source transfer ISSUE original valuation does not "
                "equal destination transfer RECEIPT valuation"
            )
        )

    corrected_receipt_value = (
        receipt_value
        + routed_delta
    )

    if corrected_receipt_value < ZERO:
        raise (
            PurchaseValueCorrectionMovingAverageTransferDestinationReplayIntegrityError(
                "Routed PVC delta would make destination "
                "transfer receipt value negative"
            )
        )

    preceding = tuple(
        movement
        for movement in active_movements
        if movement.id < receipt.id
    )

    if preceding:
        previous = preceding[-1]

        opening_quantity = _decimal(
            previous.balance_quantity_after,
            field=(
                "destination previous MA "
                "balance_quantity_after"
            ),
        )

        opening_inventory_value = _decimal(
            previous.balance_value_after,
            field=(
                "destination previous MA "
                "balance_value_after"
            ),
        )
    else:
        opening_quantity = ZERO
        opening_inventory_value = ZERO

    later = tuple(
        movement
        for movement in active_movements
        if movement.id > receipt.id
    )

    replay_movements = tuple(
        _build_replay_movement(
            company_id=company_id,
            movement=movement,
            inventory_cost_entries_by_document_line_id=(
                inventory_cost_entries_by_document_line_id
            ),
        )
        for movement in later
    )

    return (
        PurchaseValueCorrectionMovingAverageTransferDestinationReplaySource(
            warehouse_transfer_valuation_layer_id=(
                _positive_id(
                    route.warehouse_transfer_valuation_layer_id,
                    field=(
                        "route."
                        "warehouse_transfer_valuation_layer_id"
                    ),
                )
            ),
            source_inventory_cost_entry_id=(
                _positive_id(
                    route.source_inventory_cost_entry_id,
                    field=(
                        "route.source_inventory_cost_entry_id"
                    ),
                )
            ),
            destination_inventory_cost_entry_id=(
                _positive_id(
                    route.destination_inventory_cost_entry_id,
                    field=(
                        "route."
                        "destination_inventory_cost_entry_id"
                    ),
                )
            ),
            company_id=company_id,
            product_id=product_id,
            warehouse_id=warehouse_id,
            destination_receipt_moving_average_movement_id=(
                _positive_id(
                    receipt.id,
                    field=(
                        "destination receipt "
                        "MovingAverageMovement.id"
                    ),
                )
            ),
            opening_quantity=opening_quantity,
            opening_inventory_value=(
                opening_inventory_value
            ),
            receipt_quantity=receipt_quantity,
            original_receipt_value=receipt_value,
            corrected_receipt_value=(
                corrected_receipt_value
            ),
            historical_receipt_balance_quantity_after=(
                _decimal(
                    receipt.balance_quantity_after,
                    field=(
                        "destination receipt "
                        "balance_quantity_after"
                    ),
                )
            ),
            historical_receipt_balance_value_after=(
                _decimal(
                    receipt.balance_value_after,
                    field=(
                        "destination receipt "
                        "balance_value_after"
                    ),
                )
            ),
            historical_receipt_average_unit_cost_after=(
                _decimal(
                    receipt.average_unit_cost_after,
                    field=(
                        "destination receipt "
                        "average_unit_cost_after"
                    ),
                )
            ),
            later_movements=replay_movements,
        )
    )


def calculate_moving_average_transfer_destination_replay(
    source: (
        PurchaseValueCorrectionMovingAverageTransferDestinationReplaySource
    ),
) -> (
    PurchaseValueCorrectionMovingAverageTransferDestinationReplay
):
    """
    Run destination continuation through the EXISTING MA calculator.
    """

    replay_result = (
        calculate_purchase_value_correction_moving_average_replay(
            opening_quantity=source.opening_quantity,
            opening_inventory_value=(
                source.opening_inventory_value
            ),
            receipt_quantity=source.receipt_quantity,
            original_receipt_value=(
                source.original_receipt_value
            ),
            corrected_receipt_value=(
                source.corrected_receipt_value
            ),
            historical_receipt_balance_quantity_after=(
                source
                .historical_receipt_balance_quantity_after
            ),
            historical_receipt_balance_value_after=(
                source
                .historical_receipt_balance_value_after
            ),
            historical_receipt_average_unit_cost_after=(
                source
                .historical_receipt_average_unit_cost_after
            ),
            later_movements=(
                source.later_movements
            ),
        )
    )

    return (
        PurchaseValueCorrectionMovingAverageTransferDestinationReplay(
            source=source,
            replay_result=replay_result,
        )
    )


async def load_and_calculate_moving_average_transfer_destination_replay(
    db: AsyncSession,
    *,
    route: PurchaseValueCorrectionMovingAverageTransferRoute,
    source_transfer_original_valuation_amount,
    source_transfer_corrected_valuation_amount,
) -> (
    PurchaseValueCorrectionMovingAverageTransferDestinationReplay
):
    """
    Load the exact destination MA stream and calculate the routed
    correction continuation.

    Caller owns COMMIT / ROLLBACK.
    """

    company_id = _positive_id(
        route.company_id,
        field="route.company_id",
    )

    stream_movements = tuple(
        (
            await db.execute(
                select(
                    replay_source_loader
                    .MovingAverageMovement
                )
                .where(
                    replay_source_loader
                    .MovingAverageMovement
                    .company_id
                    == company_id,
                    replay_source_loader
                    .MovingAverageMovement
                    .product_id
                    == route.product_id,
                    replay_source_loader
                    .MovingAverageMovement
                    .warehouse_id
                    == route.destination_warehouse_id,
                )
                .order_by(
                    replay_source_loader
                    .MovingAverageMovement
                    .id
                )
                .with_for_update()
            )
        ).scalars().all()
    )

    if not stream_movements:
        raise (
            PurchaseValueCorrectionMovingAverageTransferDestinationReplayNotFoundError(
                "Destination moving-average stream contains "
                "no movements"
            )
        )

    issue_line_ids = {
        movement.document_line_id
        for movement in stream_movements
        if (
            replay_source_loader._enum_value(
                movement.movement_type
            )
            == (
                replay_source_loader
                .StockMovementType
                .ISSUE
                .value
            )
            and movement.document_line_id
            is not None
        )
    }

    cost_entries_by_line = {}

    if issue_line_ids:
        cost_entries = tuple(
            (
                await db.execute(
                    select(
                        replay_source_loader
                        .InventoryCostEntry
                    )
                    .where(
                        replay_source_loader
                        .InventoryCostEntry
                        .company_id
                        == company_id,
                        replay_source_loader
                        .InventoryCostEntry
                        .document_line_id
                        .in_(
                            sorted(
                                issue_line_ids
                            )
                        ),
                    )
                    .order_by(
                        replay_source_loader
                        .InventoryCostEntry
                        .id
                    )
                    .with_for_update()
                )
            ).scalars().all()
        )

        for cost_entry in cost_entries:
            line_id = (
                cost_entry.document_line_id
            )

            if line_id in cost_entries_by_line:
                raise (
                    PurchaseValueCorrectionMovingAverageTransferDestinationReplayIntegrityError(
                        "Multiple destination InventoryCostEntry "
                        "rows exist for one document line"
                    )
                )

            cost_entries_by_line[
                line_id
            ] = cost_entry

    source = (
        build_moving_average_transfer_destination_replay_source(
            route=route,
            source_transfer_original_valuation_amount=(
                source_transfer_original_valuation_amount
            ),
            source_transfer_corrected_valuation_amount=(
                source_transfer_corrected_valuation_amount
            ),
            stream_movements=stream_movements,
            inventory_cost_entries_by_document_line_id=(
                cost_entries_by_line
            ),
        )
    )

    return (
        calculate_moving_average_transfer_destination_replay(
            source
        )
    )
