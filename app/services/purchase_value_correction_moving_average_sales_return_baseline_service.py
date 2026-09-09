from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.purchase_value_correction_moving_average_peer_replay_service import (
    calculate_purchase_value_correction_moving_average_peer_replay,
)
from app.services.purchase_value_correction_moving_average_peer_source_loader import (
    load_purchase_value_correction_moving_average_peer_source,
)
from app.services.purchase_value_correction_moving_average_sales_return_source_loader import (
    PurchaseValueCorrectionMovingAverageSalesReturnSource,
)
from app.services.purchase_value_correction_moving_average_sales_return_target_service import (
    MovingAverageSalesReturnBaselineIssuedImpact,
    MovingAverageSalesReturnBaselineOnHandImpact,
)


ZERO = Decimal("0")


class PurchaseValueCorrectionMovingAverageSalesReturnBaselineError(
    Exception
):
    """Fresh pre-return PVC MA baseline cannot be resolved safely."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionMovingAverageSalesReturnAllocationBaseline:
    allocation_event_id: int

    issued: (
        MovingAverageSalesReturnBaselineIssuedImpact
        | None
    )

    on_hand: (
        MovingAverageSalesReturnBaselineOnHandImpact
        | None
    )


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionMovingAverageSalesReturnBaselineResult:
    allocations: tuple[
        PurchaseValueCorrectionMovingAverageSalesReturnAllocationBaseline,
        ...,
    ]


def _decimal(
    value,
) -> Decimal:
    return Decimal(str(value))


def _peer_result_for_source(
    source,
):
    base = source.base_source

    return (
        calculate_purchase_value_correction_moving_average_peer_replay(
            opening_quantity=base.opening_quantity,
            opening_inventory_value=(
                base.opening_inventory_value
            ),
            receipt_quantity=base.receipt_quantity,
            original_receipt_value=(
                base.original_receipt_value
            ),
            historical_receipt_balance_quantity_after=(
                base.historical_receipt_balance_quantity_after
            ),
            historical_receipt_balance_value_after=(
                base.historical_receipt_balance_value_after
            ),
            historical_receipt_average_unit_cost_after=(
                base.historical_receipt_average_unit_cost_after
            ),
            later_movements=base.later_movements,
            peers=source.peers,
        )
    )


async def load_purchase_value_correction_moving_average_sales_return_baseline(
    db: AsyncSession,
    *,
    company_id: int,
    return_source: (
        PurchaseValueCorrectionMovingAverageSalesReturnSource
    ),
) -> PurchaseValueCorrectionMovingAverageSalesReturnBaselineResult:
    """
    Calculate a FRESH PVC baseline from immutable MA chronology.

    Do not infer baseline from current active PVC replay events,
    because those may already contain Sales Return overlay state.

    For the currently supported architecture, all active PVC
    allocations affecting this returned historical ISSUE must
    belong to ONE physical purchase-receipt peer group.

    Cross-receipt nonlinear MA correction remains fail-closed.

    No writes.
    No COMMIT / ROLLBACK.
    """

    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if not isinstance(
        return_source,
        PurchaseValueCorrectionMovingAverageSalesReturnSource,
    ):
        raise (
            PurchaseValueCorrectionMovingAverageSalesReturnBaselineError(
                "return_source has invalid type"
            )
        )

    allocation_ids = tuple(
        sorted(
            set(
                return_source
                .historical_allocation_event_ids
            )
        )
    )

    if not allocation_ids:
        return (
            PurchaseValueCorrectionMovingAverageSalesReturnBaselineResult(
                allocations=(),
            )
        )

    peer_sources = []

    for allocation_id in allocation_ids:
        try:
            source = (
                await load_purchase_value_correction_moving_average_peer_source(
                    db,
                    company_id=company_id,
                    allocation_event_id=allocation_id,
                )
            )
        except Exception:
            # A historical allocation may no longer be active.
            # It cannot contribute to current PVC baseline.
            continue

        peer_sources.append(
            source
        )

    if not peer_sources:
        return (
            PurchaseValueCorrectionMovingAverageSalesReturnBaselineResult(
                allocations=(),
            )
        )

    # Every independently loaded active allocation must resolve to
    # the same physical receipt root. This preserves the current
    # same-root limitation and fails closed before cross-receipt
    # nonlinear replay is implemented.
    root_keys = set()

    for source in peer_sources:
        base = source.base_source

        root_keys.add(
            (
                getattr(
                    base,
                    "source_receipt_moving_average_movement_id",
                    None,
                ),
                getattr(
                    base,
                    "product_id",
                    None,
                ),
                getattr(
                    base,
                    "warehouse_id",
                    None,
                ),
            )
        )

    if len(root_keys) != 1:
        raise (
            PurchaseValueCorrectionMovingAverageSalesReturnBaselineError(
                "Sales Return PVC baseline spans multiple "
                "physical MA receipt roots; cross-receipt "
                "aggregate replay is not implemented"
            )
        )

    # One load already contains all active peers for this receipt.
    source = peer_sources[0]

    peer_result = _peer_result_for_source(
        source
    )

    by_allocation = {}

    for step in peer_result.steps:
        allocation_id = (
            step.peer.allocation_event_id
        )

        issued = None
        on_hand = None

        for impact in step.attributed_impacts:
            effect_kind = str(
                impact.effect_kind
            )

            quantity = _decimal(
                impact.quantity
            )

            original_value = _decimal(
                impact.original_valuation_amount
            )

            corrected_value = _decimal(
                impact.corrected_valuation_amount
            )

            if effect_kind == "issued":
                if (
                    impact.source_inventory_cost_entry_id
                    != return_source.inventory_cost_entry_id
                ):
                    continue

                if issued is not None:
                    raise (
                        PurchaseValueCorrectionMovingAverageSalesReturnBaselineError(
                            "Multiple baseline issued impacts "
                            "exist for one allocation and exact ICE"
                        )
                    )

                if (
                    impact.source_moving_average_movement_id
                    is None
                    or impact.source_inventory_cost_entry_id
                    is None
                ):
                    raise (
                        PurchaseValueCorrectionMovingAverageSalesReturnBaselineError(
                            "Baseline issued impact has "
                            "incomplete provenance"
                        )
                    )

                issued = (
                    MovingAverageSalesReturnBaselineIssuedImpact(
                        allocation_event_id=allocation_id,
                        source_moving_average_movement_id=(
                            impact.source_moving_average_movement_id
                        ),
                        source_inventory_cost_entry_id=(
                            impact.source_inventory_cost_entry_id
                        ),
                        source_issue_quantity=quantity,
                        original_valuation_amount=original_value,
                        corrected_valuation_amount=corrected_value,
                        currency_code=(
                            source.base_source.currency_code
                        ),
                    )
                )

            elif effect_kind == "on_hand":
                if on_hand is not None:
                    raise (
                        PurchaseValueCorrectionMovingAverageSalesReturnBaselineError(
                            "Multiple baseline on_hand impacts "
                            "exist for one allocation"
                        )
                    )

                on_hand = (
                    MovingAverageSalesReturnBaselineOnHandImpact(
                        allocation_event_id=allocation_id,
                        quantity=quantity,
                        original_valuation_amount=original_value,
                        corrected_valuation_amount=corrected_value,
                        currency_code=(
                            source.base_source.currency_code
                        ),
                    )
                )

            else:
                raise (
                    PurchaseValueCorrectionMovingAverageSalesReturnBaselineError(
                        "Unsupported peer replay effect kind"
                    )
                )

        # If this allocation no longer affects the returned ICE,
        # no return overlay is required for it.
        if issued is None:
            continue

        by_allocation[
            allocation_id
        ] = (
            PurchaseValueCorrectionMovingAverageSalesReturnAllocationBaseline(
                allocation_event_id=allocation_id,
                issued=issued,
                on_hand=on_hand,
            )
        )

    return (
        PurchaseValueCorrectionMovingAverageSalesReturnBaselineResult(
            allocations=tuple(
                by_allocation[key]
                for key in sorted(
                    by_allocation
                )
            ),
        )
    )
