from datetime import date
from decimal import Decimal

import pytest

from app.services.sales_return_cost_calculation_service import (
    FIFO,
    WEIGHTED_AVERAGE_MOVING,
    SalesReturnCostCandidate,
    SalesReturnCostCapacityError,
    SalesReturnCostChronologyError,
    SalesReturnCostDataIntegrityError,
    SalesReturnCostMethodError,
    SalesReturnFifoCostSlice,
    SalesReturnIssueCostSource,
    build_sales_return_cost_targets,
)


ISSUE_DATE = date(
    2026,
    8,
    1,
)

RETURN_DATE_1 = date(
    2026,
    8,
    10,
)

RETURN_DATE_2 = date(
    2026,
    8,
    20,
)


def moving_source(
    *,
    quantity="3",
    valuation="300.00000000",
    cost="300.00",
    unit_cost="100.00000000",
):
    return SalesReturnIssueCostSource(
        source_id=50,
        issue_date=ISSUE_DATE,
        valuation_method=(
            WEIGHTED_AVERAGE_MOVING
        ),
        quantity=Decimal(
            quantity
        ),
        unit_cost=Decimal(
            unit_cost
        ),
        valuation_amount=Decimal(
            valuation
        ),
        cost_amount=Decimal(
            cost
        ),
    )


def fifo_source():
    return SalesReturnIssueCostSource(
        source_id=60,
        issue_date=ISSUE_DATE,
        valuation_method=FIFO,
        quantity=Decimal(
            "3"
        ),
        unit_cost=Decimal(
            "110.00000000"
        ),
        valuation_amount=Decimal(
            "330.00000000"
        ),
        cost_amount=Decimal(
            "330.00"
        ),
    )


def fifo_slices():
    return (
        SalesReturnFifoCostSlice(
            source_id=101,
            stock_lot_id=1001,
            quantity=Decimal(
                "2"
            ),
            unit_cost=Decimal(
                "100.00000000"
            ),
        ),
        SalesReturnFifoCostSlice(
            source_id=102,
            stock_lot_id=1002,
            quantity=Decimal(
                "1"
            ),
            unit_cost=Decimal(
                "130.00000000"
            ),
        ),
    )


def candidate(
    *,
    return_id=10,
    event_date=RETURN_DATE_1,
    quantity="1",
):
    return SalesReturnCostCandidate(
        return_source_id=return_id,
        event_date=event_date,
        quantity=Decimal(
            quantity
        ),
    )


def test_moving_average_full_return_restores_exact_source_cost():
    result = build_sales_return_cost_targets(
        source=moving_source(),
        candidates=(
            candidate(
                quantity="3"
            ),
        ),
    )

    assert len(
        result
    ) == 1

    target = result[0]

    assert (
        target.inventory_cost_entry_id
        == 50
    )

    assert (
        target.restored_quantity
        == Decimal(
            "3"
        )
    )

    assert (
        target.restored_valuation_amount
        == Decimal(
            "300.00000000"
        )
    )

    assert (
        target.restored_cost_amount
        == Decimal(
            "300.00"
        )
    )

    assert (
        target.aggregate_historical_unit_cost
        == Decimal(
            "100.00000000"
        )
    )

    assert target.fifo_slices == ()


def test_moving_average_partial_return_uses_historical_issue_cost():
    result = build_sales_return_cost_targets(
        source=moving_source(),
        candidates=(
            candidate(
                quantity="1"
            ),
        ),
    )

    assert (
        result[0].restored_cost_amount
        == Decimal(
            "100.00"
        )
    )

    assert (
        result[0].aggregate_historical_unit_cost
        == Decimal(
            "100.00000000"
        )
    )


def test_moving_average_multiple_returns_conserve_money_rounding():
    source = moving_source(
        quantity="3",
        valuation="1.00000000",
        cost="1.00",
        unit_cost="0.33333333",
    )

    result = build_sales_return_cost_targets(
        source=source,
        candidates=(
            candidate(
                return_id=10,
                event_date=RETURN_DATE_1,
                quantity="1",
            ),
            candidate(
                return_id=11,
                event_date=RETURN_DATE_2,
                quantity="2",
            ),
        ),
    )

    assert tuple(
        target.restored_cost_amount
        for target in result
    ) == (
        Decimal(
            "0.33"
        ),
        Decimal(
            "0.67"
        ),
    )

    assert sum(
        (
            target.restored_cost_amount
            for target in result
        ),
        Decimal(
            "0.00"
        ),
    ) == Decimal(
        "1.00"
    )


def test_candidates_are_deterministically_sorted():
    result = build_sales_return_cost_targets(
        source=moving_source(),
        candidates=(
            candidate(
                return_id=20,
                event_date=RETURN_DATE_2,
            ),
            candidate(
                return_id=10,
                event_date=RETURN_DATE_1,
            ),
        ),
    )

    assert tuple(
        target.return_source_id
        for target in result
    ) == (
        10,
        20,
    )


def test_return_before_issue_is_rejected():
    with pytest.raises(
        SalesReturnCostChronologyError
    ):
        build_sales_return_cost_targets(
            source=moving_source(),
            candidates=(
                candidate(
                    event_date=date(
                        2026,
                        7,
                        31,
                    )
                ),
            ),
        )


def test_overreturn_is_rejected():
    with pytest.raises(
        SalesReturnCostCapacityError
    ):
        build_sales_return_cost_targets(
            source=moving_source(),
            candidates=(
                candidate(
                    quantity="4"
                ),
            ),
        )


def test_duplicate_return_source_is_rejected():
    with pytest.raises(
        SalesReturnCostDataIntegrityError,
        match="Duplicate return_source_id",
    ):
        build_sales_return_cost_targets(
            source=moving_source(),
            candidates=(
                candidate(
                    return_id=10,
                ),
                candidate(
                    return_id=10,
                    event_date=RETURN_DATE_2,
                ),
            ),
        )


def test_nonpositive_return_quantity_is_rejected():
    with pytest.raises(
        SalesReturnCostDataIntegrityError,
        match="Returned quantity",
    ):
        build_sales_return_cost_targets(
            source=moving_source(),
            candidates=(
                candidate(
                    quantity="0"
                ),
            ),
        )


def test_unsupported_method_is_rejected():
    source = SalesReturnIssueCostSource(
        source_id=50,
        issue_date=ISSUE_DATE,
        valuation_method="standard_cost",
        quantity=Decimal(
            "1"
        ),
        unit_cost=Decimal(
            "100"
        ),
        valuation_amount=Decimal(
            "100"
        ),
        cost_amount=Decimal(
            "100"
        ),
    )

    with pytest.raises(
        SalesReturnCostMethodError
    ):
        build_sales_return_cost_targets(
            source=source,
            candidates=(
                candidate(),
            ),
        )


def test_source_money_rounding_inconsistency_is_rejected():
    source = moving_source(
        valuation="100.00400000",
        cost="100.01",
        unit_cost="33.33466667",
    )

    with pytest.raises(
        SalesReturnCostDataIntegrityError,
        match="cost_amount",
    ):
        build_sales_return_cost_targets(
            source=source,
            candidates=(
                candidate(),
            ),
        )


def test_source_unit_cost_inconsistency_is_rejected():
    source = moving_source(
        unit_cost="99.00000000",
    )

    with pytest.raises(
        SalesReturnCostDataIntegrityError,
        match="unit_cost",
    ):
        build_sales_return_cost_targets(
            source=source,
            candidates=(
                candidate(),
            ),
        )


def test_fifo_requires_consumption_slices():
    with pytest.raises(
        SalesReturnCostDataIntegrityError,
        match="requires original",
    ):
        build_sales_return_cost_targets(
            source=fifo_source(),
            candidates=(
                candidate(),
            ),
        )


def test_moving_average_rejects_fifo_slices():
    with pytest.raises(
        SalesReturnCostDataIntegrityError,
        match="cannot contain FIFO",
    ):
        build_sales_return_cost_targets(
            source=moving_source(),
            candidates=(
                candidate(),
            ),
            fifo_slices=fifo_slices(),
        )


def test_fifo_slice_quantity_must_match_issue_quantity():
    bad = (
        SalesReturnFifoCostSlice(
            source_id=101,
            stock_lot_id=1001,
            quantity=Decimal(
                "1"
            ),
            unit_cost=Decimal(
                "100"
            ),
        ),
        SalesReturnFifoCostSlice(
            source_id=102,
            stock_lot_id=1002,
            quantity=Decimal(
                "1"
            ),
            unit_cost=Decimal(
                "130"
            ),
        ),
    )

    with pytest.raises(
        SalesReturnCostDataIntegrityError,
        match="quantities do not match",
    ):
        build_sales_return_cost_targets(
            source=fifo_source(),
            candidates=(
                candidate(),
            ),
            fifo_slices=bad,
        )


def test_fifo_slice_cost_must_match_inventory_cost_entry():
    bad = (
        SalesReturnFifoCostSlice(
            source_id=101,
            stock_lot_id=1001,
            quantity=Decimal(
                "2"
            ),
            unit_cost=Decimal(
                "90"
            ),
        ),
        SalesReturnFifoCostSlice(
            source_id=102,
            stock_lot_id=1002,
            quantity=Decimal(
                "1"
            ),
            unit_cost=Decimal(
                "130"
            ),
        ),
    )

    with pytest.raises(
        SalesReturnCostDataIntegrityError,
        match="historical cost",
    ):
        build_sales_return_cost_targets(
            source=fifo_source(),
            candidates=(
                candidate(),
            ),
            fifo_slices=bad,
        )


def test_fifo_duplicate_consumption_id_is_rejected():
    bad = (
        SalesReturnFifoCostSlice(
            source_id=101,
            stock_lot_id=1001,
            quantity=Decimal(
                "2"
            ),
            unit_cost=Decimal(
                "100"
            ),
        ),
        SalesReturnFifoCostSlice(
            source_id=101,
            stock_lot_id=1002,
            quantity=Decimal(
                "1"
            ),
            unit_cost=Decimal(
                "130"
            ),
        ),
    )

    with pytest.raises(
        SalesReturnCostDataIntegrityError,
        match="Duplicate FIFO consumption",
    ):
        build_sales_return_cost_targets(
            source=fifo_source(),
            candidates=(
                candidate(),
            ),
            fifo_slices=bad,
        )


def test_fifo_partial_return_restores_last_consumed_slice_first():
    result = build_sales_return_cost_targets(
        source=fifo_source(),
        candidates=(
            candidate(
                quantity="1"
            ),
        ),
        fifo_slices=fifo_slices(),
    )

    target = result[0]

    assert (
        target.restored_quantity
        == Decimal(
            "1"
        )
    )

    assert (
        target.restored_valuation_amount
        == Decimal(
            "130.00000000"
        )
    )

    assert (
        target.restored_cost_amount
        == Decimal(
            "130.00"
        )
    )

    assert (
        target.aggregate_historical_unit_cost
        == Decimal(
            "130.00000000"
        )
    )

    assert len(
        target.fifo_slices
    ) == 1

    restored_slice = (
        target.fifo_slices[0]
    )

    assert (
        restored_slice.fifo_consumption_id
        == 102
    )

    assert (
        restored_slice.stock_lot_id
        == 1002
    )

    assert (
        restored_slice.quantity
        == Decimal(
            "1"
        )
    )


def test_fifo_two_unit_return_crosses_slices_in_reverse_order():
    result = build_sales_return_cost_targets(
        source=fifo_source(),
        candidates=(
            candidate(
                quantity="2"
            ),
        ),
        fifo_slices=fifo_slices(),
    )

    target = result[0]

    assert (
        target.restored_valuation_amount
        == Decimal(
            "230.00000000"
        )
    )

    assert (
        target.restored_cost_amount
        == Decimal(
            "230.00"
        )
    )

    assert tuple(
        (
            item.fifo_consumption_id,
            item.quantity,
        )
        for item in target.fifo_slices
    ) == (
        (
            102,
            Decimal(
                "1"
            ),
        ),
        (
            101,
            Decimal(
                "1"
            ),
        ),
    )


def test_fifo_sequential_returns_continue_restoration_history():
    result = build_sales_return_cost_targets(
        source=fifo_source(),
        candidates=(
            candidate(
                return_id=10,
                event_date=RETURN_DATE_1,
                quantity="1",
            ),
            candidate(
                return_id=11,
                event_date=RETURN_DATE_2,
                quantity="1",
            ),
        ),
        fifo_slices=fifo_slices(),
    )

    assert (
        result[0]
        .fifo_slices[0]
        .fifo_consumption_id
        == 102
    )

    assert (
        result[1]
        .fifo_slices[0]
        .fifo_consumption_id
        == 101
    )

    assert (
        result[0].restored_cost_amount
        == Decimal(
            "130.00"
        )
    )

    assert (
        result[1].restored_cost_amount
        == Decimal(
            "100.00"
        )
    )


def test_fifo_full_return_conserves_exact_issue_cost():
    result = build_sales_return_cost_targets(
        source=fifo_source(),
        candidates=(
            candidate(
                return_id=10,
                event_date=RETURN_DATE_1,
                quantity="1",
            ),
            candidate(
                return_id=11,
                event_date=RETURN_DATE_2,
                quantity="2",
            ),
        ),
        fifo_slices=fifo_slices(),
    )

    assert sum(
        (
            item.restored_quantity
            for item in result
        ),
        Decimal(
            "0"
        ),
    ) == Decimal(
        "3"
    )

    assert sum(
        (
            item.restored_valuation_amount
            for item in result
        ),
        Decimal(
            "0.00000000"
        ),
    ) == Decimal(
        "330.00000000"
    )

    assert sum(
        (
            item.restored_cost_amount
            for item in result
        ),
        Decimal(
            "0.00"
        ),
    ) == Decimal(
        "330.00"
    )


def test_fifo_full_return_restores_all_source_consumptions():
    result = build_sales_return_cost_targets(
        source=fifo_source(),
        candidates=(
            candidate(
                quantity="3"
            ),
        ),
        fifo_slices=fifo_slices(),
    )

    assert tuple(
        item.fifo_consumption_id
        for item
        in result[0].fifo_slices
    ) == (
        102,
        101,
    )

    assert tuple(
        item.quantity
        for item
        in result[0].fifo_slices
    ) == (
        Decimal(
            "1"
        ),
        Decimal(
            "2"
        ),
    )


def test_empty_return_candidates_is_noop():
    assert (
        build_sales_return_cost_targets(
            source=moving_source(),
            candidates=(),
        )
        == ()
    )


def test_target_pair_key_is_return_plus_inventory_cost_entry():
    result = build_sales_return_cost_targets(
        source=moving_source(),
        candidates=(
            candidate(
                return_id=77,
            ),
        ),
    )

    assert (
        result[0].pair_key
        == (
            77,
            50,
        )
    )
