from datetime import date
from decimal import Decimal
from pathlib import Path
import ast

import pytest

from app.services.purchase_value_correction_fifo_impact_calculation_service import (
    ActiveFifoAllocationPeerCandidate,
    ActiveFifoConsumptionCandidate,
    PurchaseValueCorrectionFifoAllocationCandidate,
    PurchaseValueCorrectionFifoImpactAmountError,
    PurchaseValueCorrectionFifoImpactQuantityError,
    PurchaseValueCorrectionFifoImpactSourceError,
    build_purchase_value_correction_fifo_impact_targets,
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

D3 = date(
    2026,
    9,
    3,
)


def allocation(
    *,
    event_id,
    ifa_id,
    quantity,
    original,
    corrected,
    event_date=D1,
):
    return (
        PurchaseValueCorrectionFifoAllocationCandidate(
            purchase_value_correction_allocation_event_id=(
                event_id
            ),
            invoice_fulfillment_allocation_id=(
                ifa_id
            ),
            recognition_date=event_date,
            quantity=Decimal(
                quantity
            ),
            original_allocated_base_amount=Decimal(
                original
            ),
            corrected_allocated_base_amount=Decimal(
                corrected
            ),
            currency_code="UAH",
        )
    )


def peer(
    *,
    ifa_id,
    quantity,
    event_date=D1,
):
    return (
        ActiveFifoAllocationPeerCandidate(
            invoice_fulfillment_allocation_id=(
                ifa_id
            ),
            receipt_event_date=event_date,
            quantity=Decimal(
                quantity
            ),
        )
    )


def consumption(
    *,
    consumption_id,
    document_id,
    line_id,
    quantity,
    event_date,
):
    return (
        ActiveFifoConsumptionCandidate(
            stock_lot_consumption_id=(
                consumption_id
            ),
            issue_document_id=(
                document_id
            ),
            issue_document_line_id=(
                line_id
            ),
            issue_event_date=event_date,
            quantity=Decimal(
                quantity
            ),
        )
    )


def build_fifo(
    **kwargs,
):
    allocations = tuple(
        kwargs.get(
            "allocation_candidates",
            (),
        )
    )

    if (
        "active_allocation_peers"
        not in kwargs
    ):
        kwargs[
            "active_allocation_peers"
        ] = tuple(
            peer(
                ifa_id=(
                    item
                    .invoice_fulfillment_allocation_id
                ),
                quantity=item.quantity,
                event_date=(
                    item.recognition_date
                ),
            )
            for item in allocations
        )

    return (
        build_purchase_value_correction_fifo_impact_targets(
            **kwargs
        )
    )


def test_all_on_hand():
    targets = (
        build_fifo(
            stock_lot_id=100,
            receipt_quantity=Decimal("10"),
            current_consumed_quantity=Decimal("0"),
            allocation_candidates=(
                allocation(
                    event_id=1,
                    ifa_id=11,
                    quantity="10",
                    original="10.00",
                    corrected="8.00",
                ),
            ),
            active_consumptions=(),
        )
    )

    assert len(
        targets
    ) == 1

    target = targets[0]

    assert target.destination_kind == "on_hand"
    assert target.quantity == Decimal("10")
    assert target.recognition_date == D1
    assert target.original_base_amount == Decimal("10.00")
    assert target.corrected_base_amount == Decimal("8.00")
    assert target.base_amount_delta == Decimal("-2.00")
    assert target.stock_lot_consumption_id is None


def test_all_issued_to_one_consumption():
    targets = (
        build_fifo(
            stock_lot_id=100,
            receipt_quantity=Decimal("10"),
            current_consumed_quantity=Decimal("10"),
            allocation_candidates=(
                allocation(
                    event_id=1,
                    ifa_id=11,
                    quantity="10",
                    original="10.00",
                    corrected="8.00",
                ),
            ),
            active_consumptions=(
                consumption(
                    consumption_id=200,
                    document_id=300,
                    line_id=301,
                    quantity="10",
                    event_date=D2,
                ),
            ),
        )
    )

    assert len(
        targets
    ) == 1

    target = targets[0]

    assert target.destination_kind == "issued"
    assert target.stock_lot_consumption_id == 200
    assert target.issue_document_id == 300
    assert target.issue_document_line_id == 301
    assert target.quantity == Decimal("10")
    assert target.recognition_date == D2


def test_issued_impact_cannot_predate_later_allocation_date():
    targets = (
        build_fifo(
            stock_lot_id=100,
            receipt_quantity=Decimal("10"),
            current_consumed_quantity=Decimal("10"),
            allocation_candidates=(
                allocation(
                    event_id=1,
                    ifa_id=11,
                    quantity="10",
                    original="10.00",
                    corrected="8.00",
                    event_date=D3,
                ),
            ),
            active_consumptions=(
                consumption(
                    consumption_id=200,
                    document_id=300,
                    line_id=301,
                    quantity="10",
                    event_date=D2,
                ),
            ),
        )
    )

    assert len(targets) == 1
    assert targets[0].destination_kind == "issued"
    assert targets[0].recognition_date == D3


def test_interval_example_30_50_20_consumed_65():
    targets = (
        build_fifo(
            stock_lot_id=100,
            receipt_quantity=Decimal("100"),
            current_consumed_quantity=Decimal("65"),
            allocation_candidates=(
                allocation(
                    event_id=1,
                    ifa_id=11,
                    quantity="30",
                    original="30.00",
                    corrected="27.00",
                ),
                allocation(
                    event_id=2,
                    ifa_id=12,
                    quantity="50",
                    original="50.00",
                    corrected="45.00",
                ),
                allocation(
                    event_id=3,
                    ifa_id=13,
                    quantity="20",
                    original="20.00",
                    corrected="18.00",
                ),
            ),
            active_consumptions=(
                consumption(
                    consumption_id=201,
                    document_id=301,
                    line_id=401,
                    quantity="40",
                    event_date=D2,
                ),
                consumption(
                    consumption_id=202,
                    document_id=302,
                    line_id=402,
                    quantity="25",
                    event_date=D3,
                ),
            ),
        )
    )

    by_source = {}

    for target in targets:
        by_source.setdefault(
            target
            .purchase_value_correction_allocation_event_id,
            [],
        ).append(
            target
        )

    first = by_source[1]
    second = by_source[2]
    third = by_source[3]

    assert tuple(
        (
            item.destination_kind,
            item.quantity,
        )
        for item in first
    ) == (
        (
            "issued",
            Decimal("30"),
        ),
    )

    assert tuple(
        (
            item.destination_kind,
            item.stock_lot_consumption_id,
            item.quantity,
        )
        for item in second
    ) == (
        (
            "issued",
            201,
            Decimal("10"),
        ),
        (
            "issued",
            202,
            Decimal("25"),
        ),
        (
            "on_hand",
            None,
            Decimal("15"),
        ),
    )

    assert tuple(
        (
            item.destination_kind,
            item.quantity,
        )
        for item in third
    ) == (
        (
            "on_hand",
            Decimal("20"),
        ),
    )


def test_active_consumptions_are_ordered_deterministically():
    targets = (
        build_fifo(
            stock_lot_id=100,
            receipt_quantity=Decimal("10"),
            current_consumed_quantity=Decimal("10"),
            allocation_candidates=(
                allocation(
                    event_id=1,
                    ifa_id=11,
                    quantity="10",
                    original="10.00",
                    corrected="8.00",
                ),
            ),
            active_consumptions=(
                consumption(
                    consumption_id=202,
                    document_id=302,
                    line_id=402,
                    quantity="5",
                    event_date=D3,
                ),
                consumption(
                    consumption_id=201,
                    document_id=301,
                    line_id=401,
                    quantity="5",
                    event_date=D2,
                ),
            ),
        )
    )

    assert tuple(
        item.stock_lot_consumption_id
        for item in targets
    ) == (
        201,
        202,
    )


def test_peer_intervals_are_ordered_by_receipt_date_then_ifa_id():
    targets = (
        build_fifo(
            stock_lot_id=100,
            receipt_quantity=Decimal("10"),
            current_consumed_quantity=Decimal("0"),
            allocation_candidates=(
                allocation(
                    event_id=2,
                    ifa_id=20,
                    quantity="5",
                    original="5.00",
                    corrected="4.00",
                    event_date=D2,
                ),
                allocation(
                    event_id=1,
                    ifa_id=10,
                    quantity="5",
                    original="5.00",
                    corrected="4.00",
                    event_date=D1,
                ),
            ),
            active_consumptions=(),
        )
    )

    assert tuple(
        item.invoice_fulfillment_allocation_id
        for item in targets
    ) == (
        10,
        20,
    )


def test_penny_rounding_before_after_is_independent_and_conserved():
    targets = (
        build_fifo(
            stock_lot_id=100,
            receipt_quantity=Decimal("2"),
            current_consumed_quantity=Decimal("1"),
            allocation_candidates=(
                allocation(
                    event_id=1,
                    ifa_id=11,
                    quantity="2",
                    original="0.03",
                    corrected="0.02",
                ),
            ),
            active_consumptions=(
                consumption(
                    consumption_id=201,
                    document_id=301,
                    line_id=401,
                    quantity="1",
                    event_date=D2,
                ),
            ),
        )
    )

    assert len(
        targets
    ) == 2

    issued = targets[0]
    on_hand = targets[1]

    assert issued.destination_kind == "issued"
    assert on_hand.destination_kind == "on_hand"

    assert (
        issued.original_base_amount
        + on_hand.original_base_amount
        == Decimal("0.03")
    )

    assert (
        issued.corrected_base_amount
        + on_hand.corrected_base_amount
        == Decimal("0.02")
    )

    assert sum(
        (
            item.base_amount_delta
            for item in targets
        ),
        Decimal("0"),
    ) == Decimal("-0.01")

    assert any(
        item.is_noop
        for item in targets
    )


def test_partial_allocation_quantity_is_allowed():
    targets = (
        build_fifo(
            stock_lot_id=100,
            receipt_quantity=Decimal("100"),
            current_consumed_quantity=Decimal("20"),
            allocation_candidates=(
                allocation(
                    event_id=1,
                    ifa_id=11,
                    quantity="30",
                    original="30.00",
                    corrected="27.00",
                ),
            ),
            active_consumptions=(
                consumption(
                    consumption_id=201,
                    document_id=301,
                    line_id=401,
                    quantity="20",
                    event_date=D2,
                ),
            ),
        )
    )

    assert sum(
        (
            item.quantity
            for item in targets
        ),
        Decimal("0"),
    ) == Decimal("30")


def test_active_consumption_total_must_equal_lot_current_consumed():
    with pytest.raises(
        PurchaseValueCorrectionFifoImpactQuantityError,
        match="does not equal",
    ):
        build_fifo(
            stock_lot_id=100,
            receipt_quantity=Decimal("10"),
            current_consumed_quantity=Decimal("6"),
            allocation_candidates=(
                allocation(
                    event_id=1,
                    ifa_id=11,
                    quantity="10",
                    original="10.00",
                    corrected="8.00",
                ),
            ),
            active_consumptions=(
                consumption(
                    consumption_id=201,
                    document_id=301,
                    line_id=401,
                    quantity="5",
                    event_date=D2,
                ),
            ),
        )


def test_current_consumed_cannot_exceed_receipt():
    with pytest.raises(
        PurchaseValueCorrectionFifoImpactQuantityError,
        match="cannot exceed",
    ):
        build_fifo(
            stock_lot_id=100,
            receipt_quantity=Decimal("10"),
            current_consumed_quantity=Decimal("11"),
            allocation_candidates=(),
            active_consumptions=(),
        )


def test_active_allocation_quantity_cannot_exceed_receipt():
    with pytest.raises(
        PurchaseValueCorrectionFifoImpactQuantityError,
        match="exceeds",
    ):
        build_fifo(
            stock_lot_id=100,
            receipt_quantity=Decimal("10"),
            current_consumed_quantity=Decimal("0"),
            allocation_candidates=(
                allocation(
                    event_id=1,
                    ifa_id=11,
                    quantity="11",
                    original="11.00",
                    corrected="10.00",
                ),
            ),
            active_consumptions=(),
        )


def test_duplicate_ifa_id_fails_closed():
    with pytest.raises(
        PurchaseValueCorrectionFifoImpactSourceError,
        match="InvoiceFulfillmentAllocation",
    ):
        build_fifo(
            stock_lot_id=100,
            receipt_quantity=Decimal("10"),
            current_consumed_quantity=Decimal("0"),
            allocation_candidates=(
                allocation(
                    event_id=1,
                    ifa_id=11,
                    quantity="5",
                    original="5.00",
                    corrected="4.00",
                ),
                allocation(
                    event_id=2,
                    ifa_id=11,
                    quantity="5",
                    original="5.00",
                    corrected="4.00",
                ),
            ),
            active_consumptions=(),
        )


def test_duplicate_consumption_id_fails_closed():
    with pytest.raises(
        PurchaseValueCorrectionFifoImpactSourceError,
        match="StockLotConsumption",
    ):
        build_fifo(
            stock_lot_id=100,
            receipt_quantity=Decimal("10"),
            current_consumed_quantity=Decimal("10"),
            allocation_candidates=(
                allocation(
                    event_id=1,
                    ifa_id=11,
                    quantity="10",
                    original="10.00",
                    corrected="8.00",
                ),
            ),
            active_consumptions=(
                consumption(
                    consumption_id=201,
                    document_id=301,
                    line_id=401,
                    quantity="5",
                    event_date=D2,
                ),
                consumption(
                    consumption_id=201,
                    document_id=302,
                    line_id=402,
                    quantity="5",
                    event_date=D3,
                ),
            ),
        )


def test_active_allocation_source_cannot_be_noop():
    with pytest.raises(
        PurchaseValueCorrectionFifoImpactAmountError,
        match="no-op",
    ):
        build_fifo(
            stock_lot_id=100,
            receipt_quantity=Decimal("10"),
            current_consumed_quantity=Decimal("0"),
            allocation_candidates=(
                allocation(
                    event_id=1,
                    ifa_id=11,
                    quantity="10",
                    original="10.00",
                    corrected="10.00",
                ),
            ),
            active_consumptions=(),
        )


def test_empty_allocations_are_allowed():
    assert (
        build_fifo(
            stock_lot_id=100,
            receipt_quantity=Decimal("10"),
            current_consumed_quantity=Decimal("0"),
            allocation_candidates=(),
            active_consumptions=(),
        )
        == ()
    )


def test_pure_service_has_no_db_or_model_imports():
    path = Path(
        "app/services/"
        "purchase_value_correction_fifo_"
        "impact_calculation_service.py"
    )

    tree = ast.parse(
        path.read_text()
    )

    for node in ast.walk(
        tree
    ):
        if isinstance(
            node,
            ast.ImportFrom,
        ):
            module = (
                node.module
                or ""
            )

            assert not (
                module == "sqlalchemy"
                or module.startswith(
                    "sqlalchemy."
                )
                or module.startswith(
                    "app.models."
                )
                or module == "app.core.database"
            )

        if not isinstance(
            node,
            ast.Call,
        ):
            continue

        if not isinstance(
            node.func,
            ast.Attribute,
        ):
            continue

        if node.func.attr in {
            "delete",
            "execute",
            "flush",
            "commit",
            "rollback",
            "add_all",
        }:
            raise AssertionError(
                "Forbidden DB-style pure-layer call: "
                f"{node.func.attr}"
            )

        if node.func.attr != "add":
            continue

        receiver = node.func.value

        if isinstance(
            receiver,
            ast.Name,
        ):
            receiver_name = receiver.id
        else:
            receiver_name = None

        assert receiver_name not in {
            "db",
            "session",
            "async_session",
        }


def test_uncorrected_peer_reserves_interval_before_corrected_ifa():
    targets = build_fifo(
        stock_lot_id=100,
        receipt_quantity=Decimal("100"),
        current_consumed_quantity=Decimal("40"),
        active_allocation_peers=(
            peer(
                ifa_id=11,
                quantity="30",
                event_date=D1,
            ),
            peer(
                ifa_id=12,
                quantity="50",
                event_date=D1,
            ),
            peer(
                ifa_id=13,
                quantity="20",
                event_date=D1,
            ),
        ),
        allocation_candidates=(
            allocation(
                event_id=2,
                ifa_id=12,
                quantity="50",
                original="50.00",
                corrected="45.00",
                event_date=D3,
            ),
        ),
        active_consumptions=(
            consumption(
                consumption_id=201,
                document_id=301,
                line_id=401,
                quantity="40",
                event_date=D2,
            ),
        ),
    )

    assert tuple(
        (
            item.destination_kind,
            item.quantity,
            item.recognition_date,
        )
        for item in targets
    ) == (
        (
            "issued",
            Decimal("10"),
            D3,
        ),
        (
            "on_hand",
            Decimal("40"),
            D3,
        ),
    )

    assert sum(
        (
            item.original_base_amount
            for item in targets
        ),
        Decimal("0"),
    ) == Decimal("50.00")

    assert sum(
        (
            item.corrected_base_amount
            for item in targets
        ),
        Decimal("0"),
    ) == Decimal("45.00")


def test_correction_quantity_must_match_active_peer_quantity():
    with pytest.raises(
        PurchaseValueCorrectionFifoImpactQuantityError,
        match="does not match",
    ):
        build_fifo(
            stock_lot_id=100,
            receipt_quantity=Decimal("100"),
            current_consumed_quantity=Decimal("0"),
            active_allocation_peers=(
                peer(
                    ifa_id=11,
                    quantity="30",
                    event_date=D1,
                ),
            ),
            allocation_candidates=(
                allocation(
                    event_id=1,
                    ifa_id=11,
                    quantity="20",
                    original="20.00",
                    corrected="18.00",
                    event_date=D2,
                ),
            ),
            active_consumptions=(),
        )
