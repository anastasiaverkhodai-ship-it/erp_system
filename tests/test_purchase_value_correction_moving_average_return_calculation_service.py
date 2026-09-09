from decimal import Decimal

import pytest

from app.services.purchase_value_correction_moving_average_return_calculation_service import (
    MovingAverageReturnCandidate,
    PurchaseValueCorrectionMovingAverageReturnCalculationError,
    calculate_moving_average_return_pvc_reclassification,
)


def test_partial_return_reclassifies_existing_issued_pvc_impact():
    result = calculate_moving_average_return_pvc_reclassification(
        source_issue_quantity=Decimal("40"),
        issued_original_valuation_amount=Decimal("400"),
        issued_corrected_valuation_amount=Decimal("360"),
        return_candidates=(
            MovingAverageReturnCandidate(
                return_source_id=101,
                return_quantity=Decimal("15"),
            ),
        ),
    )

    assert result.returned_quantity == Decimal("15")
    assert result.remaining_issued_quantity == Decimal("25")

    assert (
        result.remaining_issued_original_valuation_amount
        == Decimal("250.00000000")
    )
    assert (
        result.remaining_issued_corrected_valuation_amount
        == Decimal("225.00000000")
    )

    assert (
        result.migrated_on_hand_original_valuation_amount
        == Decimal("150.00000000")
    )
    assert (
        result.migrated_on_hand_corrected_valuation_amount
        == Decimal("135.00000000")
    )

    assert (
        result.allocations[0].migrated_valuation_delta
        == Decimal("-15.00000000")
    )


def test_full_return_moves_entire_issued_impact_to_on_hand():
    result = calculate_moving_average_return_pvc_reclassification(
        source_issue_quantity=Decimal("40"),
        issued_original_valuation_amount=Decimal("400"),
        issued_corrected_valuation_amount=Decimal("360"),
        return_candidates=(
            MovingAverageReturnCandidate(
                return_source_id=101,
                return_quantity=Decimal("40"),
            ),
        ),
    )

    assert result.remaining_issued_quantity == Decimal("0")
    assert (
        result.remaining_issued_original_valuation_amount
        == Decimal("0")
    )
    assert (
        result.remaining_issued_corrected_valuation_amount
        == Decimal("0")
    )

    assert (
        result.migrated_on_hand_original_valuation_amount
        == Decimal("400")
    )
    assert (
        result.migrated_on_hand_corrected_valuation_amount
        == Decimal("360")
    )


def test_multiple_partial_returns_use_cumulative_allocation():
    result = calculate_moving_average_return_pvc_reclassification(
        source_issue_quantity=Decimal("3"),
        issued_original_valuation_amount=Decimal("10"),
        issued_corrected_valuation_amount=Decimal("9"),
        return_candidates=(
            MovingAverageReturnCandidate(
                return_source_id=101,
                return_quantity=Decimal("1"),
            ),
            MovingAverageReturnCandidate(
                return_source_id=102,
                return_quantity=Decimal("1"),
            ),
            MovingAverageReturnCandidate(
                return_source_id=103,
                return_quantity=Decimal("1"),
            ),
        ),
    )

    assert len(result.allocations) == 3

    assert (
        sum(
            (
                item.issued_original_valuation_amount
                for item in result.allocations
            ),
            Decimal("0"),
        )
        == Decimal("10")
    )

    assert (
        sum(
            (
                item.issued_corrected_valuation_amount
                for item in result.allocations
            ),
            Decimal("0"),
        )
        == Decimal("9")
    )

    assert result.remaining_issued_quantity == Decimal("0")


def test_source_is_issued_impact_not_purchase_receipt_value():
    """
    This is the architectural regression.

    Historical ISSUE cost may differ from purchase receipt cost
    because other moving-average activity can exist between the
    receipt and this ISSUE.

    PVC return allocation therefore uses the already-replayed
    ISSUE valuation, not receipt value / receipt quantity.
    """

    result = calculate_moving_average_return_pvc_reclassification(
        source_issue_quantity=Decimal("40"),
        issued_original_valuation_amount=Decimal("520"),
        issued_corrected_valuation_amount=Decimal("500"),
        return_candidates=(
            MovingAverageReturnCandidate(
                return_source_id=101,
                return_quantity=Decimal("10"),
            ),
        ),
    )

    assert (
        result.migrated_on_hand_original_valuation_amount
        == Decimal("130.00000000")
    )
    assert (
        result.migrated_on_hand_corrected_valuation_amount
        == Decimal("125.00000000")
    )
    assert (
        result.allocations[0].migrated_valuation_delta
        == Decimal("-5.00000000")
    )


def test_price_increase_reclassifies_positive_pvc_delta():
    result = calculate_moving_average_return_pvc_reclassification(
        source_issue_quantity=Decimal("40"),
        issued_original_valuation_amount=Decimal("400"),
        issued_corrected_valuation_amount=Decimal("440"),
        return_candidates=(
            MovingAverageReturnCandidate(
                return_source_id=101,
                return_quantity=Decimal("15"),
            ),
        ),
    )

    assert (
        result.allocations[0].migrated_valuation_delta
        == Decimal("15.00000000")
    )


def test_return_cannot_exceed_issue_capacity():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageReturnCalculationError
    ):
        calculate_moving_average_return_pvc_reclassification(
            source_issue_quantity=Decimal("40"),
            issued_original_valuation_amount=Decimal("400"),
            issued_corrected_valuation_amount=Decimal("360"),
            return_candidates=(
                MovingAverageReturnCandidate(
                    return_source_id=101,
                    return_quantity=Decimal("41"),
                ),
            ),
        )


def test_duplicate_return_source_is_rejected():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageReturnCalculationError
    ):
        calculate_moving_average_return_pvc_reclassification(
            source_issue_quantity=Decimal("40"),
            issued_original_valuation_amount=Decimal("400"),
            issued_corrected_valuation_amount=Decimal("360"),
            return_candidates=(
                MovingAverageReturnCandidate(
                    return_source_id=101,
                    return_quantity=Decimal("10"),
                ),
                MovingAverageReturnCandidate(
                    return_source_id=101,
                    return_quantity=Decimal("5"),
                ),
            ),
        )


def test_zero_return_set_is_valid_noop():
    result = calculate_moving_average_return_pvc_reclassification(
        source_issue_quantity=Decimal("40"),
        issued_original_valuation_amount=Decimal("400"),
        issued_corrected_valuation_amount=Decimal("360"),
        return_candidates=(),
    )

    assert result.returned_quantity == Decimal("0")
    assert result.remaining_issued_quantity == Decimal("40")
    assert (
        result.remaining_issued_original_valuation_amount
        == Decimal("400")
    )
    assert (
        result.remaining_issued_corrected_valuation_amount
        == Decimal("360")
    )
    assert result.allocations == ()
