from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.sales_return_cost_restoration_reconciliation_service as service

from app.services.sales_return_cost_calculation_service import (
    FIFO,
    WEIGHTED_AVERAGE_MOVING,
    SalesReturnCostCandidate,
    SalesReturnCostTarget,
    SalesReturnFifoCostSlice,
    SalesReturnIssueCostSource,
)
from app.services.sales_return_cost_restoration_reconciliation_service import (
    build_sales_return_cost_restoration_reconciliation_targets,
    reconcile_sales_return_cost_restoration_for_fulfillment_line,
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
    return_id,
    quantity="1",
    valuation="100",
    cost="100",
    event_date=D1,
):
    return SalesReturnCostTarget(
        return_source_id=return_id,
        inventory_cost_entry_id=50,
        event_date=event_date,
        valuation_method=(
            WEIGHTED_AVERAGE_MOVING
        ),
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
            "100"
        ),
        fifo_slices=(),
    )


def test_exact_current_is_omitted():
    value = target(
        return_id=10
    )

    assert (
        build_sales_return_cost_restoration_reconciliation_targets(
            desired_targets=(
                value,
            ),
            current_targets=(
                value,
            ),
        )
        == ()
    )


def test_new_target_is_added():
    value = target(
        return_id=10
    )

    assert (
        build_sales_return_cost_restoration_reconciliation_targets(
            desired_targets=(
                value,
            ),
            current_targets=(),
        )
        == (
            value,
        )
    )


def test_removed_current_becomes_zero_target():
    current = target(
        return_id=10
    )

    result = (
        build_sales_return_cost_restoration_reconciliation_targets(
            desired_targets=(),
            current_targets=(
                current,
            ),
        )
    )

    assert len(
        result
    ) == 1

    zero = result[0]

    assert zero.pair_key == current.pair_key

    assert (
        zero.restored_quantity
        == Decimal(
            "0"
        )
    )

    assert (
        zero.restored_cost_amount
        == Decimal(
            "0"
        )
    )

    assert zero.fifo_slices == ()


def test_changed_target_is_emitted():
    current = target(
        return_id=10,
        quantity="1",
    )

    desired = target(
        return_id=10,
        quantity="2",
        valuation="200",
        cost="200",
    )

    assert (
        build_sales_return_cost_restoration_reconciliation_targets(
            desired_targets=(
                desired,
            ),
            current_targets=(
                current,
            ),
        )
        == (
            desired,
        )
    )


def test_removal_precedes_new_source():
    current = target(
        return_id=20,
        event_date=D2,
    )

    desired = target(
        return_id=10,
        event_date=D1,
    )

    result = (
        build_sales_return_cost_restoration_reconciliation_targets(
            desired_targets=(
                desired,
            ),
            current_targets=(
                current,
            ),
        )
    )

    assert tuple(
        item.return_source_id
        for item in result
    ) == (
        20,
        10,
    )

    assert (
        result[0].restored_quantity
        == Decimal(
            "0"
        )
    )


@pytest.mark.asyncio
async def test_main_reconciliation_uses_pure_math_then_persistence(
    monkeypatch,
):
    fulfillment_line = SimpleNamespace(
        id=2,
        product_id=100,
    )

    cost_entry = SimpleNamespace(
        id=50,
    )

    source = SalesReturnIssueCostSource(
        source_id=50,
        issue_date=D1,
        valuation_method=(
            WEIGHTED_AVERAGE_MOVING
        ),
        quantity=Decimal(
            "2"
        ),
        unit_cost=Decimal(
            "100"
        ),
        valuation_amount=Decimal(
            "200"
        ),
        cost_amount=Decimal(
            "200"
        ),
    )

    candidates = (
        SalesReturnCostCandidate(
            return_source_id=10,
            event_date=D2,
            quantity=Decimal(
                "1"
            ),
        ),
    )

    desired = (
        SalesReturnCostTarget(
            return_source_id=10,
            inventory_cost_entry_id=50,
            event_date=D2,
            valuation_method=(
                WEIGHTED_AVERAGE_MOVING
            ),
            restored_quantity=Decimal(
                "1"
            ),
            restored_valuation_amount=Decimal(
                "100"
            ),
            restored_cost_amount=Decimal(
                "100"
            ),
            aggregate_historical_unit_cost=Decimal(
                "100"
            ),
            fifo_slices=(),
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_source_context",
        AsyncMock(
            return_value=(
                fulfillment_line,
                cost_entry,
                source,
                (),
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_active_return_candidates",
        AsyncMock(
            return_value=candidates
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_current_targets",
        AsyncMock(
            return_value=()
        ),
    )

    build = AsyncMock()

    def fake_build(
        *,
        source,
        candidates,
        fifo_slices,
    ):
        assert source.source_id == 50

        assert candidates == (
            candidates_value
        )

        assert fifo_slices == ()

        return desired

    candidates_value = candidates

    monkeypatch.setattr(
        service,
        "build_sales_return_cost_targets",
        fake_build,
    )

    created_event = SimpleNamespace(
        id=700
    )

    reconcile = AsyncMock(
        return_value=(
            created_event,
        )
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_return_cost_restoration_source",
        reconcile,
    )

    result = (
        await reconcile_sales_return_cost_restoration_for_fulfillment_line(
            object(),
            company_id=1,
            fulfillment_id=1,
            fulfillment_line_id=2,
            created_by=7,
            adjustment_date=D2,
        )
    )

    assert (
        result.inventory_cost_entry_id
        == 50
    )

    assert result.return_candidates == candidates

    assert result.desired_targets == desired

    assert (
        result.reconciliation_targets
        == desired
    )

    assert (
        result.created_events
        == (
            created_event,
        )
    )

    reconcile.assert_awaited_once()

    kwargs = (
        reconcile.await_args.kwargs
    )

    assert kwargs[
        "company_id"
    ] == 1

    assert kwargs[
        "target"
    ] == desired[0]

    assert kwargs[
        "created_by"
    ] == 7

    assert kwargs[
        "reversal_date"
    ] == D2


@pytest.mark.asyncio
async def test_exact_rerun_creates_nothing(
    monkeypatch,
):
    fulfillment_line = SimpleNamespace(
        id=2,
        product_id=100,
    )

    cost_entry = SimpleNamespace(
        id=50,
    )

    source = SalesReturnIssueCostSource(
        source_id=50,
        issue_date=D1,
        valuation_method=(
            WEIGHTED_AVERAGE_MOVING
        ),
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

    candidate_value = (
        SalesReturnCostCandidate(
            return_source_id=10,
            event_date=D2,
            quantity=Decimal(
                "1"
            ),
        ),
    )

    desired = target(
        return_id=10,
        event_date=D2,
    )

    monkeypatch.setattr(
        service,
        "_load_source_context",
        AsyncMock(
            return_value=(
                fulfillment_line,
                cost_entry,
                source,
                (),
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_active_return_candidates",
        AsyncMock(
            return_value=(
                candidate_value
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "build_sales_return_cost_targets",
        lambda **kwargs: (
            desired,
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_current_targets",
        AsyncMock(
            return_value=(
                desired,
            )
        ),
    )

    reconcile = AsyncMock()

    monkeypatch.setattr(
        service,
        "reconcile_sales_return_cost_restoration_source",
        reconcile,
    )

    result = (
        await reconcile_sales_return_cost_restoration_for_fulfillment_line(
            object(),
            company_id=1,
            fulfillment_id=1,
            fulfillment_line_id=2,
            created_by=7,
        )
    )

    assert result.reconciliation_targets == ()

    assert result.created_events == ()

    reconcile.assert_not_awaited()


def test_fifo_target_change_detects_slice_change():
    current = SalesReturnCostTarget(
        return_source_id=10,
        inventory_cost_entry_id=50,
        event_date=D1,
        valuation_method=FIFO,
        restored_quantity=Decimal(
            "1"
        ),
        restored_valuation_amount=Decimal(
            "130"
        ),
        restored_cost_amount=Decimal(
            "130"
        ),
        aggregate_historical_unit_cost=Decimal(
            "130"
        ),
        fifo_slices=(),
    )

    desired = SalesReturnCostTarget(
        return_source_id=10,
        inventory_cost_entry_id=50,
        event_date=D1,
        valuation_method=FIFO,
        restored_quantity=Decimal(
            "1"
        ),
        restored_valuation_amount=Decimal(
            "130"
        ),
        restored_cost_amount=Decimal(
            "130"
        ),
        aggregate_historical_unit_cost=Decimal(
            "130"
        ),
        fifo_slices=(
            SimpleNamespace(
                fifo_consumption_id=100,
                stock_lot_id=1000,
                quantity=Decimal(
                    "1"
                ),
                unit_cost=Decimal(
                    "130"
                ),
                valuation_amount=Decimal(
                    "130"
                ),
            ),
        ),
    )

    result = (
        build_sales_return_cost_restoration_reconciliation_targets(
            desired_targets=(
                desired,
            ),
            current_targets=(
                current,
            ),
        )
    )

    assert result == (
        desired,
    )
