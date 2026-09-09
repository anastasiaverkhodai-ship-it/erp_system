from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


ZERO = Decimal("0")
VALUATION_QUANTUM = Decimal("0.00000001")


class PurchaseValueCorrectionMovingAveragePeerProjectionError(
    Exception
):
    pass


class PurchaseValueCorrectionMovingAveragePeerProjectionIntegrityError(
    PurchaseValueCorrectionMovingAveragePeerProjectionError
):
    pass


class PurchaseValueCorrectionMovingAveragePeerProjectionStaleError(
    PurchaseValueCorrectionMovingAveragePeerProjectionError
):
    pass


@dataclass(
    frozen=True,
    slots=True,
)
class MovingAveragePeerOnHandOverlay:
    """
    One ACTIVE marginal on-hand PVC replay effect.

    IMPORTANT:
    corrected - original is already the marginal share attributed
    to this allocation by peer-aware cumulative replay.
    """

    allocation_event_id: int
    quantity: Decimal
    original_valuation_amount: Decimal
    corrected_valuation_amount: Decimal

    @property
    def valuation_delta(self) -> Decimal:
        return _valuation(
            self.corrected_valuation_amount
            - self.original_valuation_amount
        )


@dataclass(
    frozen=True,
    slots=True,
)
class PeerAwareEffectiveMovingAverageProjection:
    quantity: Decimal

    base_inventory_value: Decimal
    base_average_unit_cost: Decimal

    pvc_on_hand_valuation_delta: Decimal

    effective_inventory_value: Decimal
    effective_average_unit_cost: Decimal

    active_allocation_event_ids: tuple[int, ...]

    @property
    def has_pvc_overlay(self) -> bool:
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
        result = Decimal(str(value))
    except Exception as exc:
        raise (
            PurchaseValueCorrectionMovingAveragePeerProjectionIntegrityError(
                f"{field} must be Decimal-compatible"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseValueCorrectionMovingAveragePeerProjectionIntegrityError(
                f"{field} must be finite"
            )
        )

    return result


def _valuation(value) -> Decimal:
    return _decimal(
        value,
        field="valuation",
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
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise (
            PurchaseValueCorrectionMovingAveragePeerProjectionIntegrityError(
                f"{field} must be a positive integer"
            )
        )

    return value


def calculate_peer_aware_effective_moving_average_projection(
    *,
    quantity,
    base_inventory_value,
    base_average_unit_cost,
    active_on_hand_overlays: tuple[
        MovingAveragePeerOnHandOverlay,
        ...,
    ],
) -> PeerAwareEffectiveMovingAverageProjection:
    """
    Calculate current effective MA valuation from:

        immutable/base MA balance
        +
        ACTIVE marginal PVC on-hand overlays.

    Because peer reconciliation stores marginal effects rather than
    independent full corrections, overlays from different active
    allocations are now additive.

    No persistence.
    No MovingAverageBalance mutation.
    No MovingAverageMovement mutation.
    """

    quantity = _decimal(
        quantity,
        field="quantity",
    )

    base_inventory_value = _valuation(
        base_inventory_value
    )

    base_average_unit_cost = _valuation(
        base_average_unit_cost
    )

    overlays = tuple(
        active_on_hand_overlays
    )

    if quantity < ZERO:
        raise (
            PurchaseValueCorrectionMovingAveragePeerProjectionIntegrityError(
                "quantity cannot be negative"
            )
        )

    if base_inventory_value < ZERO:
        raise (
            PurchaseValueCorrectionMovingAveragePeerProjectionIntegrityError(
                "base inventory value cannot be negative"
            )
        )

    if base_average_unit_cost < ZERO:
        raise (
            PurchaseValueCorrectionMovingAveragePeerProjectionIntegrityError(
                "base average unit cost cannot be negative"
            )
        )

    if quantity == ZERO:
        if (
            base_inventory_value != ZERO
            or base_average_unit_cost != ZERO
        ):
            raise (
                PurchaseValueCorrectionMovingAveragePeerProjectionIntegrityError(
                    "zero quantity requires zero base valuation"
                )
            )

        if overlays:
            raise (
                PurchaseValueCorrectionMovingAveragePeerProjectionStaleError(
                    "zero current quantity cannot have active "
                    "on-hand PVC overlays"
                )
            )

        return (
            PeerAwareEffectiveMovingAverageProjection(
                quantity=ZERO,
                base_inventory_value=ZERO,
                base_average_unit_cost=ZERO,
                pvc_on_hand_valuation_delta=ZERO,
                effective_inventory_value=ZERO,
                effective_average_unit_cost=ZERO,
                active_allocation_event_ids=(),
            )
        )

    seen_allocations = set()
    total_delta = ZERO
    allocation_ids = []

    for overlay in overlays:
        allocation_id = _positive_id(
            overlay.allocation_event_id,
            field="allocation_event_id",
        )

        if allocation_id in seen_allocations:
            raise (
                PurchaseValueCorrectionMovingAveragePeerProjectionIntegrityError(
                    "Duplicate active on-hand overlay for "
                    "one allocation"
                )
            )

        seen_allocations.add(
            allocation_id
        )

        overlay_quantity = _decimal(
            overlay.quantity,
            field="overlay quantity",
        )

        if overlay_quantity != quantity:
            raise (
                PurchaseValueCorrectionMovingAveragePeerProjectionStaleError(
                    "Active PVC on-hand overlay quantity does "
                    "not match current MA quantity"
                )
            )

        original = _valuation(
            overlay.original_valuation_amount
        )

        corrected = _valuation(
            overlay.corrected_valuation_amount
        )

        if original < ZERO or corrected < ZERO:
            raise (
                PurchaseValueCorrectionMovingAveragePeerProjectionIntegrityError(
                    "Overlay destination valuation cannot be negative"
                )
            )

        delta = _valuation(
            corrected - original
        )

        if delta == ZERO:
            raise (
                PurchaseValueCorrectionMovingAveragePeerProjectionIntegrityError(
                    "Active peer overlay cannot be a no-op"
                )
            )

        total_delta = _valuation(
            total_delta + delta
        )

        allocation_ids.append(
            allocation_id
        )

    effective_value = _valuation(
        base_inventory_value
        + total_delta
    )

    if effective_value < ZERO:
        raise (
            PurchaseValueCorrectionMovingAveragePeerProjectionIntegrityError(
                "PVC overlay makes effective inventory value negative"
            )
        )

    effective_average = _valuation(
        effective_value / quantity
    )

    return (
        PeerAwareEffectiveMovingAverageProjection(
            quantity=quantity,
            base_inventory_value=base_inventory_value,
            base_average_unit_cost=base_average_unit_cost,
            pvc_on_hand_valuation_delta=total_delta,
            effective_inventory_value=effective_value,
            effective_average_unit_cost=effective_average,
            active_allocation_event_ids=tuple(
                sorted(allocation_ids)
            ),
        )
    )
