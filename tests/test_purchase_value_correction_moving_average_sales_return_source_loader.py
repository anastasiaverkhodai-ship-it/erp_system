from decimal import Decimal

import pytest

from app.services.purchase_value_correction_moving_average_sales_return_source_loader import (
    PurchaseValueCorrectionMovingAverageSalesReturnIssuedSource,
)


def test_issued_source_delta_decrease():
    source = (
        PurchaseValueCorrectionMovingAverageSalesReturnIssuedSource(
            allocation_event_id=11,
            replay_event_id=21,
            source_moving_average_movement_id=31,
            source_inventory_cost_entry_id=41,
            product_id=51,
            warehouse_id=61,
            quantity=Decimal("40"),
            original_valuation_amount=Decimal("40"),
            corrected_valuation_amount=Decimal("36"),
            currency_code="UAH",
        )
    )

    assert source.valuation_delta == Decimal("-4")


def test_issued_source_delta_increase():
    source = (
        PurchaseValueCorrectionMovingAverageSalesReturnIssuedSource(
            allocation_event_id=11,
            replay_event_id=21,
            source_moving_average_movement_id=31,
            source_inventory_cost_entry_id=41,
            product_id=51,
            warehouse_id=61,
            quantity=Decimal("40"),
            original_valuation_amount=Decimal("40"),
            corrected_valuation_amount=Decimal("44"),
            currency_code="UAH",
        )
    )

    assert source.valuation_delta == Decimal("4")


def test_issued_source_is_frozen():
    source = (
        PurchaseValueCorrectionMovingAverageSalesReturnIssuedSource(
            allocation_event_id=11,
            replay_event_id=21,
            source_moving_average_movement_id=31,
            source_inventory_cost_entry_id=41,
            product_id=51,
            warehouse_id=61,
            quantity=Decimal("40"),
            original_valuation_amount=Decimal("40"),
            corrected_valuation_amount=Decimal("36"),
            currency_code="UAH",
        )
    )

    with pytest.raises(
        AttributeError
    ):
        source.quantity = Decimal("20")


def test_sales_return_candidate_is_frozen():
    from datetime import date

    from app.services.purchase_value_correction_moving_average_sales_return_source_loader import (
        PurchaseValueCorrectionMovingAverageSalesReturnCandidate,
    )

    candidate = (
        PurchaseValueCorrectionMovingAverageSalesReturnCandidate(
            cost_restoration_event_id=101,
            trade_return_event_id=201,
            restoration_date=date(2026, 9, 7),
            restored_quantity=Decimal("15"),
        )
    )

    with pytest.raises(AttributeError):
        candidate.restored_quantity = Decimal("10")


def test_sales_return_source_aggregates_active_return_quantity():
    from datetime import date

    from app.services.purchase_value_correction_moving_average_sales_return_source_loader import (
        PurchaseValueCorrectionMovingAverageSalesReturnCandidate,
        PurchaseValueCorrectionMovingAverageSalesReturnSource,
    )

    source = (
        PurchaseValueCorrectionMovingAverageSalesReturnSource(
            cost_restoration_event_id=102,
            inventory_cost_entry_id=41,
            product_id=51,
            warehouse_id=61,
            historical_allocation_event_ids=(),
            active_return_candidates=(
                PurchaseValueCorrectionMovingAverageSalesReturnCandidate(
                    cost_restoration_event_id=101,
                    trade_return_event_id=201,
                    restoration_date=date(2026, 9, 6),
                    restored_quantity=Decimal("10"),
                ),
                PurchaseValueCorrectionMovingAverageSalesReturnCandidate(
                    cost_restoration_event_id=102,
                    trade_return_event_id=202,
                    restoration_date=date(2026, 9, 7),
                    restored_quantity=Decimal("5"),
                ),
            ),
            issued_sources=(),
        )
    )

    assert (
        source.total_restored_quantity
        == Decimal("15")
    )


def test_sales_return_candidate_chronology_shape():
    from datetime import date

    from app.services.purchase_value_correction_moving_average_sales_return_source_loader import (
        PurchaseValueCorrectionMovingAverageSalesReturnCandidate,
    )

    first = (
        PurchaseValueCorrectionMovingAverageSalesReturnCandidate(
            cost_restoration_event_id=101,
            trade_return_event_id=201,
            restoration_date=date(2026, 9, 6),
            restored_quantity=Decimal("10"),
        )
    )

    second = (
        PurchaseValueCorrectionMovingAverageSalesReturnCandidate(
            cost_restoration_event_id=102,
            trade_return_event_id=202,
            restoration_date=date(2026, 9, 7),
            restored_quantity=Decimal("5"),
        )
    )

    ordered = tuple(
        sorted(
            (second, first),
            key=lambda item: (
                item.restoration_date,
                item.cost_restoration_event_id,
            ),
        )
    )

    assert ordered == (
        first,
        second,
    )
