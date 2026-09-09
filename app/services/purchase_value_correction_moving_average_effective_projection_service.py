from dataclasses import dataclass
from decimal import (
    Decimal,
    ROUND_HALF_UP,
)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.purchase_value_correction_moving_average_replay_event import (
    PurchaseValueCorrectionMovingAverageReplayEvent,
)
from app.services.moving_average_inventory import (
    get_locked_moving_average_balance,
)
from app.services.purchase_value_correction_moving_average_replay_persistence_service import (
    _active_originals,
    _validate_history,
)


ZERO = Decimal("0")
VALUATION_QUANTUM = Decimal("0.00000001")


class PurchaseValueCorrectionMovingAverageProjectionError(
    Exception
):
    """Base effective MA projection failure."""


class PurchaseValueCorrectionMovingAverageProjectionIntegrityError(
    PurchaseValueCorrectionMovingAverageProjectionError
):
    """Persisted projection inputs are inconsistent."""


class PurchaseValueCorrectionMovingAverageProjectionStaleError(
    PurchaseValueCorrectionMovingAverageProjectionError
):
    """
    Immutable PVC replay is no longer synchronized with the
    current base moving-average balance.
    """


class PurchaseValueCorrectionMovingAveragePeerAggregationRequiredError(
    PurchaseValueCorrectionMovingAverageProjectionError
):
    """
    More than one active PVC allocation currently contributes
    an on-hand valuation impact to one MA stream.

    Independent impact summation is deliberately not assumed
    correct until receipt-peer aggregation semantics are proven.
    """


@dataclass(
    frozen=True,
    slots=True,
)
class EffectiveMovingAverageProjection:
    company_id: int
    product_id: int
    warehouse_id: int

    quantity: Decimal

    base_inventory_value: Decimal
    base_average_unit_cost: Decimal

    pvc_on_hand_valuation_delta: Decimal

    effective_inventory_value: Decimal
    effective_average_unit_cost: Decimal

    active_on_hand_event_ids: tuple[int, ...]
    active_allocation_event_ids: tuple[int, ...]

    @property
    def has_pvc_overlay(
        self,
    ) -> bool:
        return (
            self.pvc_on_hand_valuation_delta
            != ZERO
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
            PurchaseValueCorrectionMovingAverageProjectionIntegrityError(
                f"{field} must be Decimal-compatible"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseValueCorrectionMovingAverageProjectionIntegrityError(
                f"{field} must be finite"
            )
        )

    return result


def _valuation(
    value,
    *,
    field: str,
) -> Decimal:
    return _decimal(
        value,
        field=field,
    ).quantize(
        VALUATION_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


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
            PurchaseValueCorrectionMovingAverageProjectionIntegrityError(
                f"{field} must be a positive integer"
            )
        )

    return value


def calculate_effective_moving_average_projection(
    *,
    company_id: int,
    product_id: int,
    warehouse_id: int,
    base_quantity,
    base_inventory_value,
    base_average_unit_cost,
    active_on_hand_events: tuple[
        PurchaseValueCorrectionMovingAverageReplayEvent,
        ...,
    ],
) -> EffectiveMovingAverageProjection:
    """
    Pure current-state projection.

    BASE:
        MovingAverageBalance remains the mutable warehouse-costing
        projection generated only by normal warehouse MA operations.

    OVERLAY:
        active immutable PVC MA on_hand replay impact.

    EFFECTIVE:
        base inventory value + PVC on-hand valuation delta.

    This function performs NO database writes.

    Important safety rule:
        multiple independent active PVC allocation overlays in one
        stream are not summed yet. Moving-average replay can be
        nonlinear because ISSUE costs depend on the corrected average,
        so peer aggregation must be explicitly implemented first.

    Another safety rule:
        every active on_hand event must describe the CURRENT physical
        quantity. If a later warehouse movement happened without PVC
        replay reconciliation, projection fails closed as stale.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )
    product_id = _positive_id(
        product_id,
        field="product_id",
    )
    warehouse_id = _positive_id(
        warehouse_id,
        field="warehouse_id",
    )

    quantity = _decimal(
        base_quantity,
        field="base_quantity",
    )

    base_value = _valuation(
        base_inventory_value,
        field="base_inventory_value",
    )

    base_average = _valuation(
        base_average_unit_cost,
        field="base_average_unit_cost",
    )

    if quantity < ZERO:
        raise (
            PurchaseValueCorrectionMovingAverageProjectionIntegrityError(
                "Base MA quantity cannot be negative"
            )
        )

    if base_value < ZERO:
        raise (
            PurchaseValueCorrectionMovingAverageProjectionIntegrityError(
                "Base MA inventory value cannot be negative"
            )
        )

    if base_average < ZERO:
        raise (
            PurchaseValueCorrectionMovingAverageProjectionIntegrityError(
                "Base MA average unit cost cannot be negative"
            )
        )

    if quantity == ZERO:
        if (
            base_value != ZERO
            or base_average != ZERO
        ):
            raise (
                PurchaseValueCorrectionMovingAverageProjectionIntegrityError(
                    "Zero base MA quantity must have zero value "
                    "and zero average"
                )
            )

    active_on_hand_events = tuple(
        active_on_hand_events
    )

    allocation_ids = set()
    event_ids = set()
    overlay = ZERO

    for event in active_on_hand_events:
        event_id = _positive_id(
            event.id,
            field="PVC MA replay event id",
        )

        if event_id in event_ids:
            raise (
                PurchaseValueCorrectionMovingAverageProjectionIntegrityError(
                    "Duplicate active PVC MA replay event id"
                )
            )

        event_ids.add(
            event_id
        )

        if (
            event.company_id
            != company_id
            or event.product_id
            != product_id
            or event.warehouse_id
            != warehouse_id
        ):
            raise (
                PurchaseValueCorrectionMovingAverageProjectionIntegrityError(
                    "PVC MA replay event belongs to another stream"
                )
            )

        if event.effect_kind != "on_hand":
            raise (
                PurchaseValueCorrectionMovingAverageProjectionIntegrityError(
                    "Effective MA projection accepts only active "
                    "on_hand replay events"
                )
            )

        if (
            event.source_moving_average_movement_id
            is not None
            or event.source_inventory_cost_entry_id
            is not None
        ):
            raise (
                PurchaseValueCorrectionMovingAverageProjectionIntegrityError(
                    "on_hand replay event cannot contain ISSUE provenance"
                )
            )

        event_quantity = _decimal(
            event.quantity,
            field="PVC MA on_hand quantity",
        )

        if event_quantity <= ZERO:
            raise (
                PurchaseValueCorrectionMovingAverageProjectionIntegrityError(
                    "PVC MA on_hand quantity must be positive"
                )
            )

        if event_quantity != quantity:
            raise (
                PurchaseValueCorrectionMovingAverageProjectionStaleError(
                    "PVC MA on_hand replay quantity does not match "
                    "current base MovingAverageBalance quantity"
                )
            )

        allocation_id = _positive_id(
            event.purchase_value_correction_allocation_event_id,
            field="PVC allocation event id",
        )

        allocation_ids.add(
            allocation_id
        )

        original_amount = _valuation(
            event.original_valuation_amount,
            field="original_valuation_amount",
        )

        corrected_amount = _valuation(
            event.corrected_valuation_amount,
            field="corrected_valuation_amount",
        )

        if (
            original_amount < ZERO
            or corrected_amount < ZERO
        ):
            raise (
                PurchaseValueCorrectionMovingAverageProjectionIntegrityError(
                    "PVC MA valuation amounts cannot be negative"
                )
            )

        if original_amount == corrected_amount:
            raise (
                PurchaseValueCorrectionMovingAverageProjectionIntegrityError(
                    "Active PVC MA replay event cannot be a no-op"
                )
            )

        overlay = _valuation(
            overlay
            + (
                corrected_amount
                - original_amount
            ),
            field="PVC on-hand overlay",
        )

    if len(
        allocation_ids
    ) > 1:
        raise (
            PurchaseValueCorrectionMovingAveragePeerAggregationRequiredError(
                "Multiple active PVC allocation overlays exist for "
                "one moving-average stream; peer aggregation is "
                "required before effective valuation can be used"
            )
        )

    if (
        quantity == ZERO
        and overlay != ZERO
    ):
        raise (
            PurchaseValueCorrectionMovingAverageProjectionStaleError(
                "Zero MA quantity cannot retain PVC on-hand overlay"
            )
        )

    effective_value = _valuation(
        base_value
        + overlay,
        field="effective_inventory_value",
    )

    if effective_value < ZERO:
        raise (
            PurchaseValueCorrectionMovingAverageProjectionIntegrityError(
                "PVC overlay would make effective MA inventory "
                "value negative"
            )
        )

    if quantity == ZERO:
        effective_average = _valuation(
            ZERO,
            field="effective_average_unit_cost",
        )
    else:
        effective_average = _valuation(
            effective_value
            / quantity,
            field="effective_average_unit_cost",
        )

    return EffectiveMovingAverageProjection(
        company_id=company_id,
        product_id=product_id,
        warehouse_id=warehouse_id,
        quantity=quantity,
        base_inventory_value=base_value,
        base_average_unit_cost=base_average,
        pvc_on_hand_valuation_delta=overlay,
        effective_inventory_value=effective_value,
        effective_average_unit_cost=effective_average,
        active_on_hand_event_ids=tuple(
            sorted(
                event_ids
            )
        ),
        active_allocation_event_ids=tuple(
            sorted(
                allocation_ids
            )
        ),
    )


async def load_effective_moving_average_projection_for_update(
    db: AsyncSession,
    *,
    company_id: int,
    product_id: int,
    warehouse_id: int,
) -> EffectiveMovingAverageProjection:
    """
    Load one current MA stream projection under row locks.

    Locks:
        MovingAverageBalance
        PVC MA immutable replay history for the stream

    Does NOT:
        mutate MovingAverageBalance
        mutate MovingAverageMovement
        create synthetic MA movements
        commit
        rollback

    Caller owns the transaction.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )
    product_id = _positive_id(
        product_id,
        field="product_id",
    )
    warehouse_id = _positive_id(
        warehouse_id,
        field="warehouse_id",
    )

    balance = (
        await get_locked_moving_average_balance(
            db=db,
            company_id=company_id,
            product_id=product_id,
            warehouse_id=warehouse_id,
        )
    )

    history = tuple(
        (
            await db.execute(
                select(
                    PurchaseValueCorrectionMovingAverageReplayEvent
                )
                .where(
                    PurchaseValueCorrectionMovingAverageReplayEvent.company_id
                    == company_id,
                    PurchaseValueCorrectionMovingAverageReplayEvent.product_id
                    == product_id,
                    PurchaseValueCorrectionMovingAverageReplayEvent.warehouse_id
                    == warehouse_id,
                )
                .order_by(
                    PurchaseValueCorrectionMovingAverageReplayEvent.id
                )
                .with_for_update()
            )
        ).scalars().all()
    )

    if history:
        _validate_history(
            history
        )

    active = (
        _active_originals(
            history
        )
        if history
        else ()
    )

    active_on_hand = tuple(
        event
        for event in active
        if event.effect_kind == "on_hand"
    )

    return (
        calculate_effective_moving_average_projection(
            company_id=company_id,
            product_id=product_id,
            warehouse_id=warehouse_id,
            base_quantity=balance.quantity,
            base_inventory_value=(
                balance.inventory_value
            ),
            base_average_unit_cost=(
                balance.average_unit_cost
            ),
            active_on_hand_events=(
                active_on_hand
            ),
        )
    )
