from datetime import date
from decimal import Decimal

import pytest

from app.models.sales_return_cost_restoration_event import (
    SalesReturnCostRestorationEvent,
)
from app.models.sales_return_cost_restoration_fifo_slice import (
    SalesReturnCostRestorationFifoSlice,
)
from app.services.sales_return_cost_calculation_service import (
    FIFO,
    WEIGHTED_AVERAGE_MOVING,
    SalesReturnCostTarget,
    SalesReturnFifoSliceTarget,
)
from app.services.sales_return_cost_restoration_persistence_service import (
    SalesReturnCostRestorationDataIntegrityError,
    _active_original_events,
    build_current_sales_return_cost_restoration_targets,
    build_sales_return_cost_restoration_source_plan,
)


D1 = date(
    2026,
    9,
    1,
)

D2 = date(
    2026,
    9,
    2,
)


def target(
    *,
    return_id=10,
    cost_id=20,
    event_date=D1,
    method=WEIGHTED_AVERAGE_MOVING,
    quantity="1",
    valuation="100.00000000",
    cost="100.00",
    unit_cost="100.00000000",
    slices=(),
):
    return SalesReturnCostTarget(
        return_source_id=return_id,
        inventory_cost_entry_id=cost_id,
        event_date=event_date,
        valuation_method=method,
        restored_quantity=Decimal(
            quantity
        ),
        restored_valuation_amount=Decimal(
            valuation
        ),
        restored_cost_amount=Decimal(
            cost
        ),
        aggregate_historical_unit_cost=Decimal(
            unit_cost
        ),
        fifo_slices=tuple(
            slices
        ),
    )


def event(
    *,
    event_id,
    return_id=10,
    cost_id=20,
    event_date=D1,
    method=WEIGHTED_AVERAGE_MOVING,
    quantity="1",
    valuation="100.00000000",
    cost="100.00",
    unit_cost="100.00000000",
    reversal_of_id=None,
):
    return SalesReturnCostRestorationEvent(
        id=event_id,
        company_id=1,
        trade_return_event_id=return_id,
        inventory_cost_entry_id=cost_id,
        restoration_date=event_date,
        valuation_method=method,
        restored_quantity=Decimal(
            quantity
        ),
        restored_valuation_amount=Decimal(
            valuation
        ),
        restored_cost_amount=Decimal(
            cost
        ),
        aggregate_historical_unit_cost=Decimal(
            unit_cost
        ),
        created_by=7,
        reversal_of_id=reversal_of_id,
    )


def child(
    *,
    child_id,
    parent_id,
    consumption_id=100,
    stock_lot_id=1000,
    quantity="1",
    unit_cost="130.00000000",
    valuation="130.00000000",
):
    return SalesReturnCostRestorationFifoSlice(
        id=child_id,
        company_id=1,
        sales_return_cost_restoration_event_id=(
            parent_id
        ),
        fifo_consumption_id=(
            consumption_id
        ),
        stock_lot_id=stock_lot_id,
        restored_quantity=Decimal(
            quantity
        ),
        historical_unit_cost=Decimal(
            unit_cost
        ),
        restored_valuation_amount=Decimal(
            valuation
        ),
    )


def test_active_original_only():
    value = event(
        event_id=1
    )

    assert (
        _active_original_events(
            (
                value,
            )
        )
        == (
            value,
        )
    )


def test_original_plus_reversal_is_inactive():
    original = event(
        event_id=1
    )

    reversal = event(
        event_id=2,
        reversal_of_id=1,
    )

    assert (
        _active_original_events(
            (
                original,
                reversal,
            )
        )
        == ()
    )


def test_original_reversal_replacement_uses_replacement():
    original = event(
        event_id=1
    )

    reversal = event(
        event_id=2,
        reversal_of_id=1,
    )

    replacement = event(
        event_id=3,
        quantity="2",
        valuation="200",
        cost="200",
    )

    assert (
        _active_original_events(
            (
                original,
                reversal,
                replacement,
            )
        )
        == (
            replacement,
        )
    )


def test_reversal_must_reference_loaded_pair_history():
    with pytest.raises(
        SalesReturnCostRestorationDataIntegrityError,
        match="outside loaded",
    ):
        _active_original_events(
            (
                event(
                    event_id=2,
                    reversal_of_id=1,
                ),
            )
        )


def test_reversal_cannot_change_pair_provenance():
    original = event(
        event_id=1
    )

    reversal = event(
        event_id=2,
        cost_id=21,
        reversal_of_id=1,
    )

    with pytest.raises(
        SalesReturnCostRestorationDataIntegrityError,
        match="provenance",
    ):
        _active_original_events(
            (
                original,
                reversal,
            )
        )


def test_current_moving_average_target_reconstructed():
    current = (
        build_current_sales_return_cost_restoration_targets(
            events=(
                event(
                    event_id=1
                ),
            ),
        )
    )

    assert current == (
        target(),
    )


def test_current_fifo_target_reconstructs_children():
    parent = event(
        event_id=1,
        method=FIFO,
        valuation="130",
        cost="130",
        unit_cost="130",
    )

    slice_row = child(
        child_id=10,
        parent_id=1,
    )

    current = (
        build_current_sales_return_cost_restoration_targets(
            events=(
                parent,
            ),
            fifo_slices=(
                slice_row,
            ),
        )
    )

    assert (
        current[0].fifo_slices
        == (
            SalesReturnFifoSliceTarget(
                fifo_consumption_id=100,
                stock_lot_id=1000,
                quantity=Decimal(
                    "1"
                ),
                unit_cost=Decimal(
                    "130.00000000"
                ),
                valuation_amount=Decimal(
                    "130.00000000"
                ),
            ),
        )
    )


def test_active_fifo_without_children_is_integrity_error():
    with pytest.raises(
        SalesReturnCostRestorationDataIntegrityError,
        match="no provenance",
    ):
        build_current_sales_return_cost_restoration_targets(
            events=(
                event(
                    event_id=1,
                    method=FIFO,
                ),
            ),
        )


def test_moving_average_with_fifo_children_is_integrity_error():
    with pytest.raises(
        SalesReturnCostRestorationDataIntegrityError,
        match="cannot have FIFO",
    ):
        build_current_sales_return_cost_restoration_targets(
            events=(
                event(
                    event_id=1,
                ),
            ),
            fifo_slices=(
                child(
                    child_id=10,
                    parent_id=1,
                ),
            ),
        )


def test_new_positive_target_creates_replacement():
    desired = target()

    plan = (
        build_sales_return_cost_restoration_source_plan(
            events=(),
            fifo_slices=(),
            target=desired,
        )
    )

    assert plan.reversal_event_ids == ()

    assert (
        plan.replacement_target
        == desired
    )


def test_exact_target_is_noop():
    desired = target()

    plan = (
        build_sales_return_cost_restoration_source_plan(
            events=(
                event(
                    event_id=1
                ),
            ),
            fifo_slices=(),
            target=desired,
        )
    )

    assert plan.reversal_event_ids == ()

    assert (
        plan.replacement_target
        is None
    )


def test_changed_target_reverses_and_replaces():
    desired = target(
        quantity="2",
        valuation="200",
        cost="200",
    )

    plan = (
        build_sales_return_cost_restoration_source_plan(
            events=(
                event(
                    event_id=1
                ),
            ),
            fifo_slices=(),
            target=desired,
        )
    )

    assert (
        plan.reversal_event_ids
        == (
            1,
        )
    )

    assert (
        plan.replacement_target
        == desired
    )


def test_zero_target_reversal_only():
    zero = target(
        quantity="0",
        valuation="0",
        cost="0",
        unit_cost="0",
    )

    plan = (
        build_sales_return_cost_restoration_source_plan(
            events=(
                event(
                    event_id=1
                ),
            ),
            fifo_slices=(),
            target=zero,
        )
    )

    assert (
        plan.reversal_event_ids
        == (
            1,
        )
    )

    assert (
        plan.replacement_target
        is None
    )


def test_zero_target_without_current_is_noop():
    zero = target(
        quantity="0",
        valuation="0",
        cost="0",
        unit_cost="0",
    )

    plan = (
        build_sales_return_cost_restoration_source_plan(
            events=(),
            fifo_slices=(),
            target=zero,
        )
    )

    assert plan.reversal_event_ids == ()

    assert plan.replacement_target is None


def test_zero_target_cannot_have_nonzero_money():
    with pytest.raises(
        SalesReturnCostRestorationDataIntegrityError,
        match="zero valuation",
    ):
        build_sales_return_cost_restoration_source_plan(
            events=(),
            fifo_slices=(),
            target=target(
                quantity="0",
                valuation="1",
                cost="0",
                unit_cost="0",
            ),
        )


def test_changed_date_for_same_source_is_rejected():
    with pytest.raises(
        SalesReturnCostRestorationDataIntegrityError,
        match="date changed",
    ):
        build_sales_return_cost_restoration_source_plan(
            events=(
                event(
                    event_id=1
                ),
            ),
            fifo_slices=(),
            target=target(
                event_date=D2,
                quantity="2",
                valuation="200",
                cost="200",
            ),
        )


def test_changed_method_for_same_source_is_rejected():
    with pytest.raises(
        SalesReturnCostRestorationDataIntegrityError,
        match="method changed",
    ):
        build_sales_return_cost_restoration_source_plan(
            events=(
                event(
                    event_id=1
                ),
            ),
            fifo_slices=(),
            target=target(
                method=FIFO,
                quantity="2",
                valuation="200",
                cost="200",
                slices=(
                    SalesReturnFifoSliceTarget(
                        fifo_consumption_id=100,
                        stock_lot_id=1000,
                        quantity=Decimal(
                            "2"
                        ),
                        unit_cost=Decimal(
                            "100"
                        ),
                        valuation_amount=Decimal(
                            "200"
                        ),
                    ),
                ),
            ),
        )
