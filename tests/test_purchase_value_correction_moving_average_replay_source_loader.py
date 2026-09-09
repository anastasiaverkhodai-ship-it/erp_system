from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.document import (
    DocumentStatus,
    DocumentType,
)
from app.models.stock_ledger import (
    StockMovementType,
)
from app.services.purchase_value_correction_moving_average_replay_source_loader import (
    PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError,
    PurchaseValueCorrectionMovingAverageReplaySourceNotFoundError,
    _build_replay_source_from_rows,
    _validate_active_original_graph,
)


def D(
    value,
):
    return Decimal(
        value
    )


def movement(
    *,
    id,
    movement_type,
    document_id,
    document_line_id,
    quantity_delta,
    value_delta,
    balance_quantity_after,
    balance_value_after,
    average_unit_cost_after,
    reversal_of_id=None,
    product_id=10,
    warehouse_id=20,
    movement_date=date(
        2026,
        9,
        1,
    ),
):
    return SimpleNamespace(
        id=id,
        company_id=1,
        document_id=document_id,
        document_line_id=document_line_id,
        product_id=product_id,
        warehouse_id=warehouse_id,
        movement_type=movement_type,
        movement_date=movement_date,
        quantity_delta=D(
            quantity_delta
        ),
        value_delta=D(
            value_delta
        ),
        unit_cost=D(
            "0"
        ),
        balance_quantity_after=D(
            balance_quantity_after
        ),
        balance_value_after=D(
            balance_value_after
        ),
        average_unit_cost_after=D(
            average_unit_cost_after
        ),
        reversal_of_id=reversal_of_id,
    )


def base_rows(
    *,
    original_allocated="400",
    corrected_allocated="360",
    stream_movements=None,
    cost_entries=None,
):
    allocation_event = SimpleNamespace(
        id=501,
        company_id=1,
        invoice_fulfillment_allocation_id=301,
        original_allocated_base_amount=D(
            original_allocated
        ),
        corrected_allocated_base_amount=D(
            corrected_allocated
        ),
        recognition_date=date(
            2026,
            9,
            5,
        ),
        currency_code="UAH",
        reversal_of_id=None,
    )

    allocation = SimpleNamespace(
        id=301,
        company_id=1,
        fulfillment_line_id=201,
        product_id=10,
        quantity=D(
            "40"
        ),
        status="active",
    )

    fulfillment_line = SimpleNamespace(
        id=201,
        company_id=1,
        warehouse_document_id=100,
        warehouse_document_line_id=101,
        product_id=10,
        warehouse_id=20,
        quantity=D(
            "100"
        ),
    )

    receipt = SimpleNamespace(
        id=100,
        company_id=1,
        document_type=DocumentType.RECEIPT,
        status=DocumentStatus.POSTED,
        document_date=date(
            2026,
            9,
            1,
        ),
    )

    if stream_movements is None:
        stream_movements = (
            movement(
                id=1,
                movement_type=(
                    StockMovementType.RECEIPT
                ),
                document_id=100,
                document_line_id=101,
                quantity_delta="100",
                value_delta="1000",
                balance_quantity_after="100",
                balance_value_after="1000",
                average_unit_cost_after="10",
            ),
            movement(
                id=2,
                movement_type=(
                    StockMovementType.ISSUE
                ),
                document_id=110,
                document_line_id=111,
                quantity_delta="-40",
                value_delta="-400",
                balance_quantity_after="60",
                balance_value_after="600",
                average_unit_cost_after="10",
                movement_date=date(
                    2026,
                    9,
                    3,
                ),
            ),
        )

    if cost_entries is None:
        cost_entries = {
            111: SimpleNamespace(
                id=900,
                company_id=1,
                document_id=110,
                document_line_id=111,
                valuation_method=(
                    "weighted_average_moving"
                ),
            )
        }

    return (
        allocation_event,
        allocation,
        fulfillment_line,
        receipt,
        tuple(
            stream_movements
        ),
        cost_entries,
    )


def build(
    **kwargs,
):
    (
        allocation_event,
        allocation,
        fulfillment_line,
        receipt,
        stream_movements,
        cost_entries,
    ) = base_rows(
        **kwargs
    )

    return _build_replay_source_from_rows(
        company_id=1,
        allocation_event=allocation_event,
        allocation=allocation,
        fulfillment_line=fulfillment_line,
        receipt_document=receipt,
        stream_movements=stream_movements,
        inventory_cost_entries_by_document_line_id=(
            cost_entries
        ),
    )


def test_partial_pvca_delta_changes_complete_receipt_value():
    source = build(
        original_allocated="400",
        corrected_allocated="360",
    )

    assert (
        source.receipt_quantity
        == D(
            "100"
        )
    )

    assert (
        source.original_receipt_value
        == D(
            "1000"
        )
    )

    # Critical contract:
    # complete MA receipt 1000 + allocation delta -40.
    assert (
        source.corrected_receipt_value
        == D(
            "960"
        )
    )

    assert (
        source.allocation_value_delta
        == D(
            "-40"
        )
    )


def test_loader_builds_issue_replay_provenance():
    source = build()

    assert (
        source.source_receipt_moving_average_movement_id
        == 1
    )

    assert len(
        source.later_movements
    ) == 1

    issue = source.later_movements[
        0
    ]

    assert (
        issue.movement_id
        == 2
    )

    assert (
        issue.movement_type
        == StockMovementType.ISSUE
    )

    assert (
        issue.inventory_cost_entry_id
        == 900
    )


def test_previous_active_movement_becomes_opening_state():
    earlier = movement(
        id=1,
        movement_type=(
            StockMovementType.RECEIPT
        ),
        document_id=90,
        document_line_id=91,
        quantity_delta="50",
        value_delta="250",
        balance_quantity_after="50",
        balance_value_after="250",
        average_unit_cost_after="5",
        movement_date=date(
            2026,
            8,
            30,
        ),
    )

    receipt = movement(
        id=2,
        movement_type=(
            StockMovementType.RECEIPT
        ),
        document_id=100,
        document_line_id=101,
        quantity_delta="100",
        value_delta="1000",
        balance_quantity_after="150",
        balance_value_after="1250",
        average_unit_cost_after=(
            "8.33333333"
        ),
    )

    source = build(
        stream_movements=(
            earlier,
            receipt,
        ),
        cost_entries={},
    )

    assert (
        source.opening_quantity
        == D(
            "50"
        )
    )

    assert (
        source.opening_inventory_value
        == D(
            "250"
        )
    )


def test_reversed_later_issue_is_excluded():
    receipt = movement(
        id=1,
        movement_type=(
            StockMovementType.RECEIPT
        ),
        document_id=100,
        document_line_id=101,
        quantity_delta="100",
        value_delta="1000",
        balance_quantity_after="100",
        balance_value_after="1000",
        average_unit_cost_after="10",
    )

    issue = movement(
        id=2,
        movement_type=(
            StockMovementType.ISSUE
        ),
        document_id=110,
        document_line_id=111,
        quantity_delta="-40",
        value_delta="-400",
        balance_quantity_after="60",
        balance_value_after="600",
        average_unit_cost_after="10",
    )

    reversal = movement(
        id=3,
        movement_type=(
            StockMovementType.REVERSAL
        ),
        document_id=110,
        document_line_id=111,
        quantity_delta="40",
        value_delta="400",
        balance_quantity_after="100",
        balance_value_after="1000",
        average_unit_cost_after="10",
        reversal_of_id=2,
    )

    source = build(
        stream_movements=(
            receipt,
            issue,
            reversal,
        ),
        cost_entries={},
    )

    assert (
        source.later_movements
        == ()
    )


def test_reversal_of_reversal_fails_closed():
    original = movement(
        id=1,
        movement_type=(
            StockMovementType.RECEIPT
        ),
        document_id=100,
        document_line_id=101,
        quantity_delta="100",
        value_delta="1000",
        balance_quantity_after="100",
        balance_value_after="1000",
        average_unit_cost_after="10",
    )

    reversal = movement(
        id=2,
        movement_type=(
            StockMovementType.REVERSAL
        ),
        document_id=100,
        document_line_id=101,
        quantity_delta="-100",
        value_delta="-1000",
        balance_quantity_after="0",
        balance_value_after="0",
        average_unit_cost_after="0",
        reversal_of_id=1,
    )

    bad = movement(
        id=3,
        movement_type=(
            StockMovementType.REVERSAL
        ),
        document_id=100,
        document_line_id=101,
        quantity_delta="100",
        value_delta="1000",
        balance_quantity_after="100",
        balance_value_after="1000",
        average_unit_cost_after="10",
        reversal_of_id=2,
    )

    with pytest.raises(
        PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError,
        match="another reversal|REVERSAL",
    ):
        _validate_active_original_graph(
            (
                original,
                reversal,
                bad,
            )
        )


def test_multiple_reversals_fail_closed():
    original = movement(
        id=1,
        movement_type=(
            StockMovementType.RECEIPT
        ),
        document_id=100,
        document_line_id=101,
        quantity_delta="100",
        value_delta="1000",
        balance_quantity_after="100",
        balance_value_after="1000",
        average_unit_cost_after="10",
    )

    r1 = movement(
        id=2,
        movement_type=(
            StockMovementType.REVERSAL
        ),
        document_id=100,
        document_line_id=101,
        quantity_delta="-100",
        value_delta="-1000",
        balance_quantity_after="0",
        balance_value_after="0",
        average_unit_cost_after="0",
        reversal_of_id=1,
    )

    r2 = movement(
        id=3,
        movement_type=(
            StockMovementType.REVERSAL
        ),
        document_id=100,
        document_line_id=101,
        quantity_delta="-100",
        value_delta="-1000",
        balance_quantity_after="0",
        balance_value_after="0",
        average_unit_cost_after="0",
        reversal_of_id=1,
    )

    with pytest.raises(
        PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError,
        match="multiple reversals",
    ):
        _validate_active_original_graph(
            (
                original,
                r1,
                r2,
            )
        )


def test_active_issue_requires_inventory_cost_entry():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageReplaySourceNotFoundError,
        match="InventoryCostEntry",
    ):
        build(
            cost_entries={}
        )


def test_wrong_inventory_cost_valuation_method_fails():
    wrong = {
        111: SimpleNamespace(
            id=900,
            company_id=1,
            document_id=110,
            document_line_id=111,
            valuation_method="fifo",
        )
    }

    with pytest.raises(
        PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError,
        match="valuation method",
    ):
        build(
            cost_entries=wrong
        )


def test_complete_receipt_value_cannot_become_negative():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageReplaySourceIntegrityError,
        match="negative",
    ):
        build(
            original_allocated="2000",
            corrected_allocated="0",
        )


def test_later_adjustment_is_preserved_for_calculator_fail_closed():
    receipt = movement(
        id=1,
        movement_type=(
            StockMovementType.RECEIPT
        ),
        document_id=100,
        document_line_id=101,
        quantity_delta="100",
        value_delta="1000",
        balance_quantity_after="100",
        balance_value_after="1000",
        average_unit_cost_after="10",
    )

    adjustment = movement(
        id=2,
        movement_type=(
            StockMovementType.ADJUSTMENT
        ),
        document_id=120,
        document_line_id=121,
        quantity_delta="0",
        value_delta="20",
        balance_quantity_after="100",
        balance_value_after="1020",
        average_unit_cost_after="10.2",
    )

    source = build(
        stream_movements=(
            receipt,
            adjustment,
        ),
        cost_entries={},
    )

    assert len(
        source.later_movements
    ) == 1

    assert (
        source.later_movements[
            0
        ].movement_type
        == StockMovementType.ADJUSTMENT
    )
