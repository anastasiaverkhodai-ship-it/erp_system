from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.purchase_value_correction_fifo_transfer_routing_service import (
    PurchaseValueCorrectionFifoTransferRoute,
)
from app.services.purchase_value_correction_fifo_transfer_topology_service import (
    PurchaseValueCorrectionFifoTransferTopologyIntegrityError,
    build_fifo_transfer_destination_topology,
    load_fifo_transfer_destination_topology,
)


class _FakeScalars:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return _FakeScalars(
            self._rows
        )

    def all(self):
        return list(
            self._rows
        )


class _FakeDb:
    def __init__(
        self,
        *,
        lot_rows,
        consumption_rows,
    ):
        self.results = [
            list(lot_rows),
            [
                (
                    row,
                    row.issue_event_date,
                )
                for row in consumption_rows
            ],
        ]
        self.execute_calls = 0

    async def execute(
        self,
        statement,
    ):
        self.execute_calls += 1

        if not self.results:
            raise AssertionError(
                "Unexpected DB execute"
            )

        return _FakeResult(
            self.results.pop(0)
        )


def _route(
    *,
    quantity=Decimal("5.0000"),
):
    return PurchaseValueCorrectionFifoTransferRoute(
        warehouse_transfer_valuation_layer_id=501,
        company_id=1,
        transfer_line_id=601,
        product_id=10,
        destination_warehouse_id=20,
        source_inventory_cost_entry_id=701,
        source_stock_lot_consumption_id=801,
        destination_receipt_document_id=901,
        destination_receipt_document_line_id=902,
        quantity=quantity,
        unit_cost=Decimal("12.50000000"),
        valuation_amount=Decimal("62.50000000"),
    )


def _lot(
    *,
    lot_id=1001,
    company_id=1,
    product_id=10,
    warehouse_id=20,
    source_document_id=901,
    source_document_line_id=902,
    original_quantity=Decimal("5.0000"),
    remaining_quantity=Decimal("5.0000"),
    unit_cost=Decimal("12.5000"),
):
    return SimpleNamespace(
        id=lot_id,
        company_id=company_id,
        product_id=product_id,
        warehouse_id=warehouse_id,
        source_document_id=source_document_id,
        source_document_line_id=source_document_line_id,
        original_quantity=original_quantity,
        remaining_quantity=remaining_quantity,
        unit_cost=unit_cost,
    )


def _consumption(
    *,
    consumption_id,
    lot_id=1001,
    company_id=1,
    issue_document_id,
    issue_document_line_id,
    quantity,
    issue_event_date=date(2026, 2, 10),
    unit_cost=Decimal("12.5000"),
):
    return SimpleNamespace(
        id=consumption_id,
        company_id=company_id,
        stock_lot_id=lot_id,
        issue_document_id=issue_document_id,
        issue_document_line_id=(
            issue_document_line_id
        ),
        issue_event_date=issue_event_date,
        quantity=quantity,
        unit_cost=unit_cost,
    )


def test_topology_on_hand_only():
    topology = build_fifo_transfer_destination_topology(
        route=_route(),
        stock_lot=_lot(),
        active_consumptions=(),
    )

    assert topology.classification == "on_hand"
    assert topology.destination_stock_lot_id == 1001

    assert topology.original_quantity == Decimal(
        "5.0000"
    )
    assert topology.on_hand_quantity == Decimal(
        "5.0000"
    )
    assert topology.issued_quantity == Decimal(
        "0.0000"
    )

    assert topology.issued_slices == ()


def test_topology_partially_issued():
    topology = build_fifo_transfer_destination_topology(
        route=_route(),
        stock_lot=_lot(
            remaining_quantity=Decimal("2.0000")
        ),
        active_consumptions=(
            _consumption(
                consumption_id=1101,
                issue_document_id=1201,
                issue_document_line_id=1202,
                quantity=Decimal("3.0000"),
            ),
        ),
    )

    assert topology.classification == "mixed"
    assert topology.on_hand_quantity == Decimal(
        "2.0000"
    )
    assert topology.issued_quantity == Decimal(
        "3.0000"
    )

    assert len(
        topology.issued_slices
    ) == 1

    issued = topology.issued_slices[0]

    assert issued.stock_lot_consumption_id == 1101
    assert issued.issue_document_id == 1201
    assert issued.issue_document_line_id == 1202
    assert issued.issue_event_date == date(2026, 2, 10)
    assert issued.quantity == Decimal("3.0000")


def test_topology_fully_issued_multiple_destinations():
    topology = build_fifo_transfer_destination_topology(
        route=_route(),
        stock_lot=_lot(
            remaining_quantity=Decimal("0.0000")
        ),
        active_consumptions=(
            _consumption(
                consumption_id=1101,
                issue_document_id=1201,
                issue_document_line_id=1202,
                quantity=Decimal("2.0000"),
            ),
            _consumption(
                consumption_id=1102,
                issue_document_id=1301,
                issue_document_line_id=1302,
                quantity=Decimal("3.0000"),
            ),
        ),
    )

    assert topology.classification == "issued"
    assert topology.on_hand_quantity == Decimal(
        "0.0000"
    )
    assert topology.issued_quantity == Decimal(
        "5.0000"
    )

    assert tuple(
        item.stock_lot_consumption_id
        for item in topology.issued_slices
    ) == (
        1101,
        1102,
    )


def test_topology_requires_destination_lot_identity():
    with pytest.raises(
        PurchaseValueCorrectionFifoTransferTopologyIntegrityError,
        match="receipt",
    ):
        build_fifo_transfer_destination_topology(
            route=_route(),
            stock_lot=_lot(
                source_document_line_id=999,
            ),
            active_consumptions=(),
        )


def test_topology_requires_route_quantity_equal_lot_original_quantity():
    with pytest.raises(
        PurchaseValueCorrectionFifoTransferTopologyIntegrityError,
        match="original_quantity",
    ):
        build_fifo_transfer_destination_topology(
            route=_route(
                quantity=Decimal("5.0000")
            ),
            stock_lot=_lot(
                original_quantity=Decimal("4.0000")
            ),
            active_consumptions=(),
        )


def test_topology_rejects_consumption_from_other_lot():
    with pytest.raises(
        PurchaseValueCorrectionFifoTransferTopologyIntegrityError,
        match="stock lot",
    ):
        build_fifo_transfer_destination_topology(
            route=_route(),
            stock_lot=_lot(
                remaining_quantity=Decimal("4.0000")
            ),
            active_consumptions=(
                _consumption(
                    consumption_id=1101,
                    lot_id=9999,
                    issue_document_id=1201,
                    issue_document_line_id=1202,
                    quantity=Decimal("1.0000"),
                ),
            ),
        )


def test_topology_rejects_quantity_conservation_mismatch():
    with pytest.raises(
        PurchaseValueCorrectionFifoTransferTopologyIntegrityError,
        match="conservation",
    ):
        build_fifo_transfer_destination_topology(
            route=_route(),
            stock_lot=_lot(
                remaining_quantity=Decimal("3.0000")
            ),
            active_consumptions=(
                _consumption(
                    consumption_id=1101,
                    issue_document_id=1201,
                    issue_document_line_id=1202,
                    quantity=Decimal("1.0000"),
                ),
            ),
        )


def test_topology_rejects_duplicate_consumption_ids():
    with pytest.raises(
        PurchaseValueCorrectionFifoTransferTopologyIntegrityError,
        match="duplicate",
    ):
        build_fifo_transfer_destination_topology(
            route=_route(),
            stock_lot=_lot(
                remaining_quantity=Decimal("3.0000")
            ),
            active_consumptions=(
                _consumption(
                    consumption_id=1101,
                    issue_document_id=1201,
                    issue_document_line_id=1202,
                    quantity=Decimal("1.0000"),
                ),
                _consumption(
                    consumption_id=1101,
                    issue_document_id=1301,
                    issue_document_line_id=1302,
                    quantity=Decimal("1.0000"),
                ),
            ),
        )


@pytest.mark.asyncio
async def test_loader_returns_exact_destination_topology():
    db = _FakeDb(
        lot_rows=[
            _lot(
                remaining_quantity=Decimal("2.0000")
            ),
        ],
        consumption_rows=[
            _consumption(
                consumption_id=1101,
                issue_document_id=1201,
                issue_document_line_id=1202,
                quantity=Decimal("3.0000"),
            ),
        ],
    )

    topology = (
        await load_fifo_transfer_destination_topology(
            db,
            route=_route(),
        )
    )

    assert db.execute_calls == 2

    assert topology.destination_stock_lot_id == 1001
    assert topology.classification == "mixed"
    assert topology.on_hand_quantity == Decimal(
        "2.0000"
    )
    assert topology.issued_quantity == Decimal(
        "3.0000"
    )


@pytest.mark.asyncio
async def test_loader_requires_exactly_one_destination_lot():
    db = _FakeDb(
        lot_rows=[],
        consumption_rows=[],
    )

    with pytest.raises(
        PurchaseValueCorrectionFifoTransferTopologyIntegrityError,
        match="exactly one",
    ):
        await load_fifo_transfer_destination_topology(
            db,
            route=_route(),
        )

    assert db.execute_calls == 1


@pytest.mark.asyncio
async def test_loader_rejects_ambiguous_destination_lot():
    db = _FakeDb(
        lot_rows=[
            _lot(
                lot_id=1001,
            ),
            _lot(
                lot_id=1002,
            ),
        ],
        consumption_rows=[],
    )

    with pytest.raises(
        PurchaseValueCorrectionFifoTransferTopologyIntegrityError,
        match="exactly one",
    ):
        await load_fifo_transfer_destination_topology(
            db,
            route=_route(),
        )

    assert db.execute_calls == 1
