from dataclasses import dataclass
from decimal import (
    Decimal,
    ROUND_HALF_UP,
)


ZERO = Decimal("0")
VALUATION_QUANTUM = Decimal("0.00000001")


class PurchaseValueCorrectionMovingAverageReturnCalculationError(
    Exception
):
    """Invalid PVC MA Sales Return reclassification."""


@dataclass(frozen=True)
class MovingAverageReturnCandidate:
    """
    One active Sales Return quantity assigned to one historical
    InventoryCostEntry / ISSUE source.

    Ordering must reflect immutable return chronology.
    """

    return_source_id: int
    return_quantity: Decimal


@dataclass(frozen=True)
class MovingAverageReturnPvcAllocation:
    """
    Portion of one active issued PVC MA replay impact migrated
    back to on_hand by one Sales Return.

    issued_* amounts are the portion removed from the issued
    destination.

    on_hand_* amounts are the same valuation amounts transferred
    into the on_hand destination.

    Therefore total PVC correction is conserved.
    """

    return_source_id: int
    returned_quantity: Decimal

    issued_original_valuation_amount: Decimal
    issued_corrected_valuation_amount: Decimal

    on_hand_original_valuation_amount: Decimal
    on_hand_corrected_valuation_amount: Decimal

    @property
    def migrated_valuation_delta(self) -> Decimal:
        return (
            self.issued_corrected_valuation_amount
            - self.issued_original_valuation_amount
        )


@dataclass(frozen=True)
class MovingAverageReturnPvcResult:
    """
    Pure allocation result for one active issued PVC impact.

    source_issue_quantity:
        immutable InventoryCostEntry quantity / active issued
        replay quantity capacity.

    issued_original/corrected:
        immutable valuation represented by the active issued
        PVC MA replay event before Sales Return reclassification.
    """

    source_issue_quantity: Decimal
    returned_quantity: Decimal
    remaining_issued_quantity: Decimal

    remaining_issued_original_valuation_amount: Decimal
    remaining_issued_corrected_valuation_amount: Decimal

    migrated_on_hand_original_valuation_amount: Decimal
    migrated_on_hand_corrected_valuation_amount: Decimal

    allocations: tuple[
        MovingAverageReturnPvcAllocation,
        ...,
    ]

    @property
    def source_valuation_delta(self) -> Decimal:
        return (
            self.remaining_issued_corrected_valuation_amount
            + self.migrated_on_hand_corrected_valuation_amount
            - self.remaining_issued_original_valuation_amount
            - self.migrated_on_hand_original_valuation_amount
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
            PurchaseValueCorrectionMovingAverageReturnCalculationError(
                f"{field} must be Decimal-compatible"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseValueCorrectionMovingAverageReturnCalculationError(
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
            PurchaseValueCorrectionMovingAverageReturnCalculationError(
                f"{field} must be a positive integer"
            )
        )

    return value


def _valuation(
    value: Decimal,
) -> Decimal:
    return value.quantize(
        VALUATION_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _cumulative_allocation(
    *,
    total_amount: Decimal,
    source_quantity: Decimal,
    cumulative_quantity: Decimal,
) -> Decimal:
    """
    Allocate one cumulative portion at 8-decimal valuation
    precision.

    The final full-capacity allocation is exact, avoiding
    residual rounding drift.
    """

    if cumulative_quantity == source_quantity:
        return total_amount

    return _valuation(
        total_amount
        * cumulative_quantity
        / source_quantity
    )


def calculate_moving_average_return_pvc_reclassification(
    *,
    source_issue_quantity,
    issued_original_valuation_amount,
    issued_corrected_valuation_amount,
    return_candidates,
) -> MovingAverageReturnPvcResult:
    """
    Reclassify an already-calculated active ISSUE PVC impact
    back to on_hand when quantities from that historical ISSUE
    are returned.

    IMPORTANT:

    This function does NOT reconstruct purchase-receipt cost.

    Its monetary source is the active immutable PVC MA replay
    ISSUE impact itself, whose provenance points to the exact:
        MovingAverageMovement ISSUE
        InventoryCostEntry

    This preserves nonlinear moving-average replay economics.

    Multiple partial returns use cumulative allocation so the
    complete returned quantity exactly conserves the original
    issued impact valuation.

    No DB.
    No MovingAverageBalance mutation.
    No MovingAverageMovement mutation.
    No InventoryCostEntry mutation.
    No JournalEntry.
    """

    source_quantity = _decimal(
        source_issue_quantity,
        field="source_issue_quantity",
    )

    issued_original = _decimal(
        issued_original_valuation_amount,
        field="issued_original_valuation_amount",
    )

    issued_corrected = _decimal(
        issued_corrected_valuation_amount,
        field="issued_corrected_valuation_amount",
    )

    if source_quantity <= ZERO:
        raise (
            PurchaseValueCorrectionMovingAverageReturnCalculationError(
                "source_issue_quantity must be greater than zero"
            )
        )

    if issued_original < ZERO or issued_corrected < ZERO:
        raise (
            PurchaseValueCorrectionMovingAverageReturnCalculationError(
                "issued valuation amounts cannot be negative"
            )
        )

    candidates = tuple(return_candidates)

    normalized = []
    seen_ids = set()

    for candidate in candidates:
        if not isinstance(
            candidate,
            MovingAverageReturnCandidate,
        ):
            raise (
                PurchaseValueCorrectionMovingAverageReturnCalculationError(
                    "return_candidates must contain "
                    "MovingAverageReturnCandidate values"
                )
            )

        return_source_id = _positive_id(
            candidate.return_source_id,
            field="return_source_id",
        )

        if return_source_id in seen_ids:
            raise (
                PurchaseValueCorrectionMovingAverageReturnCalculationError(
                    "Duplicate return_source_id"
                )
            )

        seen_ids.add(return_source_id)

        quantity = _decimal(
            candidate.return_quantity,
            field="return_quantity",
        )

        if quantity <= ZERO:
            raise (
                PurchaseValueCorrectionMovingAverageReturnCalculationError(
                    "return_quantity must be greater than zero"
                )
            )

        normalized.append(
            MovingAverageReturnCandidate(
                return_source_id=return_source_id,
                return_quantity=quantity,
            )
        )

    total_returned = sum(
        (
            candidate.return_quantity
            for candidate in normalized
        ),
        ZERO,
    )

    if total_returned > source_quantity:
        raise (
            PurchaseValueCorrectionMovingAverageReturnCalculationError(
                "Returned quantity exceeds historical ISSUE capacity"
            )
        )

    allocations = []

    previous_quantity = ZERO
    previous_original = ZERO
    previous_corrected = ZERO

    for candidate in normalized:
        cumulative_quantity = (
            previous_quantity
            + candidate.return_quantity
        )

        cumulative_original = _cumulative_allocation(
            total_amount=issued_original,
            source_quantity=source_quantity,
            cumulative_quantity=cumulative_quantity,
        )

        cumulative_corrected = _cumulative_allocation(
            total_amount=issued_corrected,
            source_quantity=source_quantity,
            cumulative_quantity=cumulative_quantity,
        )

        allocated_original = (
            cumulative_original
            - previous_original
        )

        allocated_corrected = (
            cumulative_corrected
            - previous_corrected
        )

        allocations.append(
            MovingAverageReturnPvcAllocation(
                return_source_id=(
                    candidate.return_source_id
                ),
                returned_quantity=(
                    candidate.return_quantity
                ),
                issued_original_valuation_amount=(
                    allocated_original
                ),
                issued_corrected_valuation_amount=(
                    allocated_corrected
                ),
                on_hand_original_valuation_amount=(
                    allocated_original
                ),
                on_hand_corrected_valuation_amount=(
                    allocated_corrected
                ),
            )
        )

        previous_quantity = cumulative_quantity
        previous_original = cumulative_original
        previous_corrected = cumulative_corrected

    remaining_quantity = (
        source_quantity
        - total_returned
    )

    remaining_original = (
        issued_original
        - previous_original
    )

    remaining_corrected = (
        issued_corrected
        - previous_corrected
    )

    result = MovingAverageReturnPvcResult(
        source_issue_quantity=source_quantity,
        returned_quantity=total_returned,
        remaining_issued_quantity=remaining_quantity,
        remaining_issued_original_valuation_amount=(
            remaining_original
        ),
        remaining_issued_corrected_valuation_amount=(
            remaining_corrected
        ),
        migrated_on_hand_original_valuation_amount=(
            previous_original
        ),
        migrated_on_hand_corrected_valuation_amount=(
            previous_corrected
        ),
        allocations=tuple(allocations),
    )

    # Exact source-level conservation.
    if (
        result.remaining_issued_original_valuation_amount
        + result.migrated_on_hand_original_valuation_amount
        != issued_original
    ):
        raise (
            PurchaseValueCorrectionMovingAverageReturnCalculationError(
                "Original issued valuation conservation failed"
            )
        )

    if (
        result.remaining_issued_corrected_valuation_amount
        + result.migrated_on_hand_corrected_valuation_amount
        != issued_corrected
    ):
        raise (
            PurchaseValueCorrectionMovingAverageReturnCalculationError(
                "Corrected issued valuation conservation failed"
            )
        )

    if (
        result.remaining_issued_quantity
        + result.returned_quantity
        != source_quantity
    ):
        raise (
            PurchaseValueCorrectionMovingAverageReturnCalculationError(
                "ISSUE quantity conservation failed"
            )
        )

    return result
