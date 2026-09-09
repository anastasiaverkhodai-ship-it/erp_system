from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from app.services.purchase_value_correction_moving_average_peer_effective_projection_service import (
    PeerAwareEffectiveMovingAverageProjection,
)


ZERO = Decimal("0")
VALUATION_QUANTUM = Decimal("0.00000001")


class PurchaseValueCorrectionMovingAverageEffectiveIssueError(
    Exception
):
    pass


class PurchaseValueCorrectionMovingAverageEffectiveIssueIntegrityError(
    PurchaseValueCorrectionMovingAverageEffectiveIssueError
):
    pass


@dataclass(
    frozen=True,
    slots=True,
)
class MovingAverageEffectiveIssueCalculation:
    issue_quantity: Decimal

    base_issue_value: Decimal
    effective_issue_value: Decimal
    pvc_issue_valuation_delta: Decimal

    base_inventory_value_before: Decimal
    effective_inventory_value_before: Decimal

    quantity_after: Decimal

    base_inventory_value_after: Decimal
    effective_inventory_value_after: Decimal

    base_average_unit_cost_after: Decimal
    effective_average_unit_cost_after: Decimal

    pvc_on_hand_valuation_delta_before: Decimal
    pvc_on_hand_valuation_delta_after: Decimal


def _decimal(
    value,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise (
            PurchaseValueCorrectionMovingAverageEffectiveIssueIntegrityError(
                f"{field} must be Decimal-compatible"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseValueCorrectionMovingAverageEffectiveIssueIntegrityError(
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


def calculate_moving_average_effective_issue(
    *,
    projection: PeerAwareEffectiveMovingAverageProjection,
    issue_quantity,
) -> MovingAverageEffectiveIssueCalculation:
    """
    Pure costing contract for the NEXT normal MA ISSUE.

    It intentionally exposes TWO values:

      base_issue_value
          cost implied by immutable/base MA history.

      effective_issue_value
          cost implied by base history + active PVC overlay.

      pvc_issue_valuation_delta
          exact difference that must migrate from current on-hand
          PVC overlay into issued PVC correction accounting.

    This function does NOT decide persistence architecture yet.

    No DB writes.
    No InventoryCostEntry mutation.
    No MovingAverageMovement mutation.
    """

    issue_quantity = _decimal(
        issue_quantity,
        field="issue_quantity",
    )

    if issue_quantity <= ZERO:
        raise (
            PurchaseValueCorrectionMovingAverageEffectiveIssueIntegrityError(
                "issue_quantity must be positive"
            )
        )

    if projection.quantity <= ZERO:
        raise (
            PurchaseValueCorrectionMovingAverageEffectiveIssueIntegrityError(
                "cannot issue from zero effective quantity"
            )
        )

    if issue_quantity > projection.quantity:
        raise (
            PurchaseValueCorrectionMovingAverageEffectiveIssueIntegrityError(
                "issue quantity exceeds current quantity"
            )
        )

    full_depletion = (
        issue_quantity
        == projection.quantity
    )

    if full_depletion:
        base_issue_value = _valuation(
            projection.base_inventory_value
        )

        effective_issue_value = _valuation(
            projection.effective_inventory_value
        )

    else:
        base_issue_value = _valuation(
            issue_quantity
            * projection.base_average_unit_cost
        )

        effective_issue_value = _valuation(
            issue_quantity
            * projection.effective_average_unit_cost
        )

    pvc_issue_delta = _valuation(
        effective_issue_value
        - base_issue_value
    )

    quantity_after = _decimal(
        projection.quantity
        - issue_quantity,
        field="quantity_after",
    )

    base_after = _valuation(
        projection.base_inventory_value
        - base_issue_value
    )

    effective_after = _valuation(
        projection.effective_inventory_value
        - effective_issue_value
    )

    if base_after < ZERO:
        raise (
            PurchaseValueCorrectionMovingAverageEffectiveIssueIntegrityError(
                "base issue costing produces negative inventory value"
            )
        )

    if effective_after < ZERO:
        raise (
            PurchaseValueCorrectionMovingAverageEffectiveIssueIntegrityError(
                "effective issue costing produces negative inventory value"
            )
        )

    pvc_overlay_after = _valuation(
        projection.pvc_on_hand_valuation_delta
        - pvc_issue_delta
    )

    if quantity_after == ZERO:
        if (
            base_after != ZERO
            or effective_after != ZERO
            or pvc_overlay_after != ZERO
        ):
            raise (
                PurchaseValueCorrectionMovingAverageEffectiveIssueIntegrityError(
                    "full depletion must consume base and PVC "
                    "valuation completely"
                )
            )

        base_average_after = ZERO
        effective_average_after = ZERO

    else:
        base_average_after = _valuation(
            base_after
            / quantity_after
        )

        effective_average_after = _valuation(
            effective_after
            / quantity_after
        )

        expected_effective = _valuation(
            base_after
            + pvc_overlay_after
        )

        if expected_effective != effective_after:
            raise (
                PurchaseValueCorrectionMovingAverageEffectiveIssueIntegrityError(
                    "effective issue conservation failed"
                )
            )

    return MovingAverageEffectiveIssueCalculation(
        issue_quantity=issue_quantity,
        base_issue_value=base_issue_value,
        effective_issue_value=effective_issue_value,
        pvc_issue_valuation_delta=pvc_issue_delta,
        base_inventory_value_before=(
            projection.base_inventory_value
        ),
        effective_inventory_value_before=(
            projection.effective_inventory_value
        ),
        quantity_after=quantity_after,
        base_inventory_value_after=base_after,
        effective_inventory_value_after=effective_after,
        base_average_unit_cost_after=(
            base_average_after
        ),
        effective_average_unit_cost_after=(
            effective_average_after
        ),
        pvc_on_hand_valuation_delta_before=(
            projection.pvc_on_hand_valuation_delta
        ),
        pvc_on_hand_valuation_delta_after=(
            pvc_overlay_after
        ),
    )
