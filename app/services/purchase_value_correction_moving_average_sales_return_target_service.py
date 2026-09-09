from dataclasses import dataclass
from decimal import Decimal

from app.services.purchase_value_correction_moving_average_return_calculation_service import (
    MovingAverageReturnCandidate,
    MovingAverageReturnPvcResult,
    calculate_moving_average_return_pvc_reclassification,
)


ZERO = Decimal("0")


class PurchaseValueCorrectionMovingAverageSalesReturnTargetError(
    Exception
):
    """Invalid return-aware PVC moving-average target state."""


@dataclass(
    frozen=True,
    slots=True,
)
class MovingAverageSalesReturnBaselineIssuedImpact:
    allocation_event_id: int
    source_moving_average_movement_id: int
    source_inventory_cost_entry_id: int

    source_issue_quantity: Decimal

    original_valuation_amount: Decimal
    corrected_valuation_amount: Decimal

    currency_code: str


@dataclass(
    frozen=True,
    slots=True,
)
class MovingAverageSalesReturnBaselineOnHandImpact:
    allocation_event_id: int

    quantity: Decimal

    original_valuation_amount: Decimal
    corrected_valuation_amount: Decimal

    currency_code: str


@dataclass(
    frozen=True,
    slots=True,
)
class MovingAverageSalesReturnTarget:
    allocation_event_id: int

    effect_kind: str

    source_moving_average_movement_id: int | None
    source_inventory_cost_entry_id: int | None

    quantity: Decimal

    original_valuation_amount: Decimal
    corrected_valuation_amount: Decimal

    currency_code: str

    @property
    def valuation_delta(
        self,
    ) -> Decimal:
        return (
            self.corrected_valuation_amount
            - self.original_valuation_amount
        )


@dataclass(
    frozen=True,
    slots=True,
)
class MovingAverageSalesReturnAllocationTargetResult:
    allocation_event_id: int

    returned_quantity: Decimal

    baseline_issued: (
        MovingAverageSalesReturnBaselineIssuedImpact
    )

    baseline_on_hand: (
        MovingAverageSalesReturnBaselineOnHandImpact | None
    )

    reclassification: MovingAverageReturnPvcResult

    desired_targets: tuple[
        MovingAverageSalesReturnTarget,
        ...,
    ]


def _decimal(
    value,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise (
            PurchaseValueCorrectionMovingAverageSalesReturnTargetError(
                f"{field} must be Decimal-compatible"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseValueCorrectionMovingAverageSalesReturnTargetError(
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
            PurchaseValueCorrectionMovingAverageSalesReturnTargetError(
                f"{field} must be a positive integer"
            )
        )

    return value


def _currency(
    value,
) -> str:
    result = str(value)

    if len(result) != 3:
        raise (
            PurchaseValueCorrectionMovingAverageSalesReturnTargetError(
                "currency_code must contain exactly 3 characters"
            )
        )

    return result


def build_purchase_value_correction_moving_average_sales_return_targets(
    *,
    baseline_issued,
    baseline_on_hand,
    active_return_candidates,
) -> MovingAverageSalesReturnAllocationTargetResult:
    """
    Build the complete desired PVC MA state for ONE allocation
    after applying the aggregate ACTIVE Sales Return history for
    one exact historical ISSUE / InventoryCostEntry.

    Monetary source:
        immutable pre-return issued PVC impact.

    Existing pre-return on_hand impact is preserved and receives
    the valuation migrated back from issued.

    This function is PURE.

    No DB.
    No MovingAverageBalance mutation.
    No MovingAverageMovement mutation.
    No InventoryCostEntry mutation.
    No JournalEntry.
    """

    if not isinstance(
        baseline_issued,
        MovingAverageSalesReturnBaselineIssuedImpact,
    ):
        raise (
            PurchaseValueCorrectionMovingAverageSalesReturnTargetError(
                "baseline_issued has invalid type"
            )
        )

    allocation_id = _positive_id(
        baseline_issued.allocation_event_id,
        field="allocation_event_id",
    )

    issue_movement_id = _positive_id(
        baseline_issued.source_moving_average_movement_id,
        field="source_moving_average_movement_id",
    )

    ice_id = _positive_id(
        baseline_issued.source_inventory_cost_entry_id,
        field="source_inventory_cost_entry_id",
    )

    source_qty = _decimal(
        baseline_issued.source_issue_quantity,
        field="source_issue_quantity",
    )

    issued_original = _decimal(
        baseline_issued.original_valuation_amount,
        field="issued original valuation",
    )

    issued_corrected = _decimal(
        baseline_issued.corrected_valuation_amount,
        field="issued corrected valuation",
    )

    currency = _currency(
        baseline_issued.currency_code
    )

    if source_qty <= ZERO:
        raise (
            PurchaseValueCorrectionMovingAverageSalesReturnTargetError(
                "source_issue_quantity must be positive"
            )
        )

    if (
        issued_original < ZERO
        or issued_corrected < ZERO
    ):
        raise (
            PurchaseValueCorrectionMovingAverageSalesReturnTargetError(
                "issued valuation cannot be negative"
            )
        )

    return_candidates = tuple(
        active_return_candidates
    )

    pure_candidates = []

    for item in return_candidates:
        try:
            return_source_id = _positive_id(
                item.return_source_id,
                field="return_source_id",
            )

            return_quantity = _decimal(
                item.return_quantity,
                field="return_quantity",
            )
        except AttributeError as exc:
            raise (
                PurchaseValueCorrectionMovingAverageSalesReturnTargetError(
                    "active return candidate has invalid shape"
                )
            ) from exc

        pure_candidates.append(
            MovingAverageReturnCandidate(
                return_source_id=return_source_id,
                return_quantity=return_quantity,
            )
        )

    reclassification = (
        calculate_moving_average_return_pvc_reclassification(
            source_issue_quantity=source_qty,
            issued_original_valuation_amount=(
                issued_original
            ),
            issued_corrected_valuation_amount=(
                issued_corrected
            ),
            return_candidates=tuple(
                pure_candidates
            ),
        )
    )

    if baseline_on_hand is None:
        on_hand_quantity = ZERO
        on_hand_original = ZERO
        on_hand_corrected = ZERO

    else:
        if not isinstance(
            baseline_on_hand,
            MovingAverageSalesReturnBaselineOnHandImpact,
        ):
            raise (
                PurchaseValueCorrectionMovingAverageSalesReturnTargetError(
                    "baseline_on_hand has invalid type"
                )
            )

        if (
            baseline_on_hand.allocation_event_id
            != allocation_id
        ):
            raise (
                PurchaseValueCorrectionMovingAverageSalesReturnTargetError(
                    "issued/on_hand allocation mismatch"
                )
            )

        if (
            _currency(
                baseline_on_hand.currency_code
            )
            != currency
        ):
            raise (
                PurchaseValueCorrectionMovingAverageSalesReturnTargetError(
                    "issued/on_hand currency mismatch"
                )
            )

        on_hand_quantity = _decimal(
            baseline_on_hand.quantity,
            field="on_hand quantity",
        )

        on_hand_original = _decimal(
            baseline_on_hand.original_valuation_amount,
            field="on_hand original valuation",
        )

        on_hand_corrected = _decimal(
            baseline_on_hand.corrected_valuation_amount,
            field="on_hand corrected valuation",
        )

        if on_hand_quantity < ZERO:
            raise (
                PurchaseValueCorrectionMovingAverageSalesReturnTargetError(
                    "on_hand quantity cannot be negative"
                )
            )

        if (
            on_hand_original < ZERO
            or on_hand_corrected < ZERO
        ):
            raise (
                PurchaseValueCorrectionMovingAverageSalesReturnTargetError(
                    "on_hand valuation cannot be negative"
                )
            )

    desired = []

    # ---------------------------------------------------------
    # ISSUED RESIDUAL
    # ---------------------------------------------------------

    if (
        reclassification.remaining_issued_quantity
        > ZERO
    ):
        issued_target = MovingAverageSalesReturnTarget(
            allocation_event_id=allocation_id,
            effect_kind="issued",
            source_moving_average_movement_id=(
                issue_movement_id
            ),
            source_inventory_cost_entry_id=ice_id,
            quantity=(
                reclassification
                .remaining_issued_quantity
            ),
            original_valuation_amount=(
                reclassification
                .remaining_issued_original_valuation_amount
            ),
            corrected_valuation_amount=(
                reclassification
                .remaining_issued_corrected_valuation_amount
            ),
            currency_code=currency,
        )

        if issued_target.valuation_delta != ZERO:
            desired.append(
                issued_target
            )

    # ---------------------------------------------------------
    # ON-HAND BASELINE + RETURNED SHARE
    # ---------------------------------------------------------

    desired_on_hand_quantity = (
        on_hand_quantity
        + reclassification.returned_quantity
    )

    desired_on_hand_original = (
        on_hand_original
        + reclassification
        .migrated_on_hand_original_valuation_amount
    )

    desired_on_hand_corrected = (
        on_hand_corrected
        + reclassification
        .migrated_on_hand_corrected_valuation_amount
    )

    if desired_on_hand_quantity > ZERO:
        on_hand_target = MovingAverageSalesReturnTarget(
            allocation_event_id=allocation_id,
            effect_kind="on_hand",
            source_moving_average_movement_id=None,
            source_inventory_cost_entry_id=None,
            quantity=desired_on_hand_quantity,
            original_valuation_amount=(
                desired_on_hand_original
            ),
            corrected_valuation_amount=(
                desired_on_hand_corrected
            ),
            currency_code=currency,
        )

        if on_hand_target.valuation_delta != ZERO:
            desired.append(
                on_hand_target
            )

    baseline_total_delta = (
        issued_corrected
        - issued_original
        + on_hand_corrected
        - on_hand_original
    )

    desired_total_delta = sum(
        (
            item.valuation_delta
            for item in desired
        ),
        ZERO,
    )

    if desired_total_delta != baseline_total_delta:
        raise (
            PurchaseValueCorrectionMovingAverageSalesReturnTargetError(
                "PVC valuation conservation failed"
            )
        )

    return (
        MovingAverageSalesReturnAllocationTargetResult(
            allocation_event_id=allocation_id,
            returned_quantity=(
                reclassification.returned_quantity
            ),
            baseline_issued=baseline_issued,
            baseline_on_hand=baseline_on_hand,
            reclassification=reclassification,
            desired_targets=tuple(
                desired
            ),
        )
    )
