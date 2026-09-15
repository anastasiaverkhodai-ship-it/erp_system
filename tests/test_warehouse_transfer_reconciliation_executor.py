from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.services.warehouse_transfer_reconciliation_executor as executor
from app.models.company import InventoryValuationMethod
from app.services.warehouse_transfer_history_service import (
    WarehouseTransferHistoryAction,
    WarehouseTransferHistoryItem,
    WarehouseTransferHistoryPlan,
    WarehouseTransferLineTarget,
    normalize_transfer_target,
)
from app.services.warehouse_transfer_physical_factory import (
    WarehouseTransferPhysicalLine,
    WarehouseTransferPhysicalResult,
    WarehouseTransferValuationLayerResult,
)


KEY = "11111111-1111-4111-8111-111111111111"
D1 = date(2026, 9, 10)
D2 = date(2026, 9, 11)


def target(
    quantity="5",
    transfer_date=D1,
):
    return normalize_transfer_target(
        company_id=1,
        source_warehouse_id=10,
        destination_warehouse_id=20,
        transfer_date=transfer_date,
        lines=(
            WarehouseTransferLineTarget(
                product_id=100,
                quantity=Decimal(quantity),
            ),
        ),
    )


def ma_physical(quantity="5"):
    quantity = Decimal(quantity)

    return WarehouseTransferPhysicalResult(
        lines=(
            WarehouseTransferPhysicalLine(
                product_id=100,
                quantity=quantity,
                issue_document_id=30,
                issue_document_line_id=31,
                receipt_document_id=40,
                valuation_layers=(
                    WarehouseTransferValuationLayerResult(
                        valuation_method=(
                            InventoryValuationMethod
                            .WEIGHTED_AVERAGE_MOVING
                        ),
                        quantity=quantity,
                        unit_cost=Decimal("11.60000000"),
                        valuation_amount=(
                            quantity
                            * Decimal("11.60000000")
                        ),
                        source_inventory_cost_entry_id=70,
                        source_stock_lot_consumption_id=None,
                        destination_receipt_document_id=40,
                        destination_receipt_document_line_id=41,
                    ),
                ),
            ),
        )
    )


def fifo_physical():
    return WarehouseTransferPhysicalResult(
        lines=(
            WarehouseTransferPhysicalLine(
                product_id=100,
                quantity=Decimal("5"),
                issue_document_id=30,
                issue_document_line_id=31,
                receipt_document_id=40,
                valuation_layers=(
                    WarehouseTransferValuationLayerResult(
                        valuation_method=(
                            InventoryValuationMethod.FIFO
                        ),
                        quantity=Decimal("3"),
                        unit_cost=Decimal("10"),
                        valuation_amount=Decimal("30"),
                        source_inventory_cost_entry_id=70,
                        source_stock_lot_consumption_id=80,
                        destination_receipt_document_id=40,
                        destination_receipt_document_line_id=41,
                    ),
                    WarehouseTransferValuationLayerResult(
                        valuation_method=(
                            InventoryValuationMethod.FIFO
                        ),
                        quantity=Decimal("2"),
                        unit_cost=Decimal("14"),
                        valuation_amount=Decimal("28"),
                        source_inventory_cost_entry_id=70,
                        source_stock_lot_consumption_id=81,
                        destination_receipt_document_id=40,
                        destination_receipt_document_line_id=42,
                    ),
                ),
            ),
        )
    )


class FakeFactory:
    def __init__(
        self,
        physical=None,
    ):
        self.physical = physical or ma_physical()
        self.calls = []

    async def create_transfer(
        self,
        db,
        *,
        target,
        created_by,
    ):
        self.calls.append("create")
        return self.physical

    async def reverse_transfer(
        self,
        db,
        *,
        original_event,
        reversal_date,
        created_by,
    ):
        self.calls.append("reverse")


@pytest.mark.asyncio
async def test_noop_has_no_physical_work(
    monkeypatch,
):
    plan = WarehouseTransferHistoryPlan(
        history_key=KEY,
        action=WarehouseTransferHistoryAction.NOOP,
        active_original=None,
        target=None,
        reversal_date=None,
    )

    async def prepare(*args, **kwargs):
        return plan

    monkeypatch.setattr(
        executor,
        "prepare_warehouse_transfer_reconciliation",
        prepare,
    )

    factory = FakeFactory()

    result = await executor.execute_warehouse_transfer_reconciliation(
        object(),
        factory=factory,
        company_id=1,
        history_key=KEY,
        target=None,
        adjustment_date=None,
        created_by=7,
    )

    assert result is plan
    assert factory.calls == []


@pytest.mark.asyncio
async def test_fifo_business_line_persists_two_layers(
    monkeypatch,
):
    calls = []

    plan = WarehouseTransferHistoryPlan(
        history_key=KEY,
        action=WarehouseTransferHistoryAction.CREATE,
        active_original=None,
        target=target(),
        reversal_date=None,
    )

    async def prepare(*args, **kwargs):
        return plan

    async def append_event(*args, **kwargs):
        calls.append("event")
        return SimpleNamespace(id=50)

    async def append_line(*args, **kwargs):
        calls.append("business_line")
        return SimpleNamespace(id=60)

    async def append_layer(*args, **kwargs):
        calls.append(
            (
                kwargs[
                    "source_stock_lot_consumption_id"
                ],
                kwargs[
                    "destination_receipt_document_line_id"
                ],
            )
        )
        return object()

    monkeypatch.setattr(
        executor,
        "prepare_warehouse_transfer_reconciliation",
        prepare,
    )
    monkeypatch.setattr(
        executor,
        "append_warehouse_transfer_event",
        append_event,
    )
    monkeypatch.setattr(
        executor,
        "append_warehouse_transfer_line",
        append_line,
    )
    monkeypatch.setattr(
        executor,
        "append_warehouse_transfer_valuation_layer",
        append_layer,
    )

    await executor.execute_warehouse_transfer_reconciliation(
        object(),
        factory=FakeFactory(
            fifo_physical()
        ),
        company_id=1,
        history_key=KEY,
        target=target(),
        adjustment_date=None,
        created_by=7,
    )

    assert calls == [
        "event",
        "business_line",
        (80, 41),
        (81, 42),
    ]


@pytest.mark.asyncio
async def test_ma_business_line_has_exactly_one_layer(
    monkeypatch,
):
    layers = []

    plan = WarehouseTransferHistoryPlan(
        history_key=KEY,
        action=WarehouseTransferHistoryAction.CREATE,
        active_original=None,
        target=target(),
        reversal_date=None,
    )

    async def prepare(*args, **kwargs):
        return plan

    async def append_event(*args, **kwargs):
        return SimpleNamespace(id=50)

    async def append_line(*args, **kwargs):
        return SimpleNamespace(id=60)

    async def append_layer(*args, **kwargs):
        layers.append(kwargs)
        return object()

    monkeypatch.setattr(
        executor,
        "prepare_warehouse_transfer_reconciliation",
        prepare,
    )
    monkeypatch.setattr(
        executor,
        "append_warehouse_transfer_event",
        append_event,
    )
    monkeypatch.setattr(
        executor,
        "append_warehouse_transfer_line",
        append_line,
    )
    monkeypatch.setattr(
        executor,
        "append_warehouse_transfer_valuation_layer",
        append_layer,
    )

    await executor.execute_warehouse_transfer_reconciliation(
        object(),
        factory=FakeFactory(),
        company_id=1,
        history_key=KEY,
        target=target(),
        adjustment_date=None,
        created_by=7,
    )

    assert len(layers) == 1
    assert (
        layers[0][
            "source_stock_lot_consumption_id"
        ]
        is None
    )


@pytest.mark.asyncio
async def test_fifo_layers_preserve_cost_not_average(
    monkeypatch,
):
    recorded = []

    plan = WarehouseTransferHistoryPlan(
        history_key=KEY,
        action=WarehouseTransferHistoryAction.CREATE,
        active_original=None,
        target=target(),
        reversal_date=None,
    )

    async def prepare(*args, **kwargs):
        return plan

    async def append_event(*args, **kwargs):
        return SimpleNamespace(id=50)

    async def append_line(*args, **kwargs):
        return SimpleNamespace(id=60)

    async def append_layer(*args, **kwargs):
        recorded.append(
            (
                kwargs["quantity"],
                kwargs["unit_cost"],
                kwargs["valuation_amount"],
            )
        )
        return object()

    monkeypatch.setattr(
        executor,
        "prepare_warehouse_transfer_reconciliation",
        prepare,
    )
    monkeypatch.setattr(
        executor,
        "append_warehouse_transfer_event",
        append_event,
    )
    monkeypatch.setattr(
        executor,
        "append_warehouse_transfer_line",
        append_line,
    )
    monkeypatch.setattr(
        executor,
        "append_warehouse_transfer_valuation_layer",
        append_layer,
    )

    await executor.execute_warehouse_transfer_reconciliation(
        object(),
        factory=FakeFactory(
            fifo_physical()
        ),
        company_id=1,
        history_key=KEY,
        target=target(),
        adjustment_date=None,
        created_by=7,
    )

    assert recorded == [
        (
            Decimal("3"),
            Decimal("10"),
            Decimal("30"),
        ),
        (
            Decimal("2"),
            Decimal("14"),
            Decimal("28"),
        ),
    ]


@pytest.mark.asyncio
async def test_layer_quantity_mismatch_rejected_before_domain_append(
    monkeypatch,
):
    bad = WarehouseTransferPhysicalResult(
        lines=(
            WarehouseTransferPhysicalLine(
                product_id=100,
                quantity=Decimal("5"),
                issue_document_id=30,
                issue_document_line_id=31,
                receipt_document_id=40,
                valuation_layers=(
                    WarehouseTransferValuationLayerResult(
                        valuation_method=(
                            InventoryValuationMethod.FIFO
                        ),
                        quantity=Decimal("3"),
                        unit_cost=Decimal("10"),
                        valuation_amount=Decimal("30"),
                        source_inventory_cost_entry_id=70,
                        source_stock_lot_consumption_id=80,
                        destination_receipt_document_id=40,
                        destination_receipt_document_line_id=41,
                    ),
                ),
            ),
        )
    )

    plan = WarehouseTransferHistoryPlan(
        history_key=KEY,
        action=WarehouseTransferHistoryAction.CREATE,
        active_original=None,
        target=target(),
        reversal_date=None,
    )

    async def prepare(*args, **kwargs):
        return plan

    appended = []

    async def append_event(*args, **kwargs):
        appended.append("event")

    monkeypatch.setattr(
        executor,
        "prepare_warehouse_transfer_reconciliation",
        prepare,
    )
    monkeypatch.setattr(
        executor,
        "append_warehouse_transfer_event",
        append_event,
    )

    with pytest.raises(
        executor.WarehouseTransferExecutionError,
        match="quantities do not sum",
    ):
        await executor.execute_warehouse_transfer_reconciliation(
            object(),
            factory=FakeFactory(bad),
            company_id=1,
            history_key=KEY,
            target=target(),
            adjustment_date=None,
            created_by=7,
        )

    assert appended == []


@pytest.mark.asyncio
async def test_fifo_layer_requires_consumption_provenance(
    monkeypatch,
):
    physical = fifo_physical()
    first = physical.lines[0]

    broken = WarehouseTransferPhysicalResult(
        lines=(
            WarehouseTransferPhysicalLine(
                product_id=100,
                quantity=Decimal("5"),
                issue_document_id=30,
                issue_document_line_id=31,
                receipt_document_id=40,
                valuation_layers=(
                    WarehouseTransferValuationLayerResult(
                        valuation_method=(
                            InventoryValuationMethod.FIFO
                        ),
                        quantity=Decimal("5"),
                        unit_cost=Decimal("11.6"),
                        valuation_amount=Decimal("58.0"),
                        source_inventory_cost_entry_id=70,
                        source_stock_lot_consumption_id=None,
                        destination_receipt_document_id=40,
                        destination_receipt_document_line_id=41,
                    ),
                ),
            ),
        )
    )

    plan = WarehouseTransferHistoryPlan(
        history_key=KEY,
        action=WarehouseTransferHistoryAction.CREATE,
        active_original=None,
        target=target(),
        reversal_date=None,
    )

    async def prepare(*args, **kwargs):
        return plan

    monkeypatch.setattr(
        executor,
        "prepare_warehouse_transfer_reconciliation",
        prepare,
    )

    with pytest.raises(
        executor.WarehouseTransferExecutionError,
        match="requires source StockLotConsumption",
    ):
        await executor.execute_warehouse_transfer_reconciliation(
            object(),
            factory=FakeFactory(broken),
            company_id=1,
            history_key=KEY,
            target=target(),
            adjustment_date=None,
            created_by=7,
        )


@pytest.mark.asyncio
async def test_ma_cannot_reference_fifo_consumption(
    monkeypatch,
):
    broken = WarehouseTransferPhysicalResult(
        lines=(
            WarehouseTransferPhysicalLine(
                product_id=100,
                quantity=Decimal("5"),
                issue_document_id=30,
                issue_document_line_id=31,
                receipt_document_id=40,
                valuation_layers=(
                    WarehouseTransferValuationLayerResult(
                        valuation_method=(
                            InventoryValuationMethod
                            .WEIGHTED_AVERAGE_MOVING
                        ),
                        quantity=Decimal("5"),
                        unit_cost=Decimal("11.6"),
                        valuation_amount=Decimal("58.0"),
                        source_inventory_cost_entry_id=70,
                        source_stock_lot_consumption_id=999,
                        destination_receipt_document_id=40,
                        destination_receipt_document_line_id=41,
                    ),
                ),
            ),
        )
    )

    plan = WarehouseTransferHistoryPlan(
        history_key=KEY,
        action=WarehouseTransferHistoryAction.CREATE,
        active_original=None,
        target=target(),
        reversal_date=None,
    )

    async def prepare(*args, **kwargs):
        return plan

    monkeypatch.setattr(
        executor,
        "prepare_warehouse_transfer_reconciliation",
        prepare,
    )

    with pytest.raises(
        executor.WarehouseTransferExecutionError,
        match="must not reference FIFO",
    ):
        await executor.execute_warehouse_transfer_reconciliation(
            object(),
            factory=FakeFactory(broken),
            company_id=1,
            history_key=KEY,
            target=target(),
            adjustment_date=None,
            created_by=7,
        )


@pytest.mark.asyncio
async def test_reversal_uses_active_original_identity(
    monkeypatch,
):
    original_target = target()

    original = WarehouseTransferHistoryItem(
        id=10,
        history_key=KEY,
        target=original_target,
        reversal_of_id=None,
    )

    plan = WarehouseTransferHistoryPlan(
        history_key=KEY,
        action=WarehouseTransferHistoryAction.REVERSE,
        active_original=original,
        target=None,
        reversal_date=D2,
    )

    async def prepare(*args, **kwargs):
        return plan

    calls = []

    class Factory(FakeFactory):
        async def reverse_transfer(
            self,
            db,
            *,
            original_event,
            reversal_date,
            created_by,
        ):
            calls.append(
                (
                    "reverse",
                    original_event.id,
                )
            )

    async def append_event(*args, **kwargs):
        calls.append(
            (
                "history",
                kwargs["reversal_of_id"],
            )
        )
        return SimpleNamespace(id=99)

    monkeypatch.setattr(
        executor,
        "prepare_warehouse_transfer_reconciliation",
        prepare,
    )
    monkeypatch.setattr(
        executor,
        "append_warehouse_transfer_event",
        append_event,
    )

    await executor.execute_warehouse_transfer_reconciliation(
        object(),
        factory=Factory(),
        company_id=1,
        history_key=KEY,
        target=None,
        adjustment_date=D2,
        created_by=7,
    )

    assert calls == [
        ("reverse", 10),
        ("history", 10),
    ]


@pytest.mark.asyncio
async def test_ma_exact_ice_amount_need_not_equal_rounded_unit_cost_product(
    monkeypatch,
):
    """
    ICE valuation_amount is exact source ISSUE truth.

    Example:
        qty 3
        effective unit cost 3.33333333
        qty * unit cost = 9.99999999
        exact ICE valuation = 10.00000000

    This must remain valid for moving average.
    """

    physical = WarehouseTransferPhysicalResult(
        lines=(
            WarehouseTransferPhysicalLine(
                product_id=100,
                quantity=Decimal("3"),
                issue_document_id=30,
                issue_document_line_id=31,
                receipt_document_id=40,
                valuation_layers=(
                    WarehouseTransferValuationLayerResult(
                        valuation_method=(
                            InventoryValuationMethod
                            .WEIGHTED_AVERAGE_MOVING
                        ),
                        quantity=Decimal("3"),
                        unit_cost=Decimal(
                            "3.33333333"
                        ),
                        valuation_amount=Decimal(
                            "10.00000000"
                        ),
                        source_inventory_cost_entry_id=70,
                        source_stock_lot_consumption_id=None,
                        destination_receipt_document_id=40,
                        destination_receipt_document_line_id=41,
                    ),
                ),
            ),
        )
    )

    ma_target = normalize_transfer_target(
        company_id=1,
        source_warehouse_id=10,
        destination_warehouse_id=20,
        transfer_date=D1,
        lines=(
            WarehouseTransferLineTarget(
                product_id=100,
                quantity=Decimal("3"),
            ),
        ),
    )

    plan = WarehouseTransferHistoryPlan(
        history_key=KEY,
        action=WarehouseTransferHistoryAction.CREATE,
        active_original=None,
        target=ma_target,
        reversal_date=None,
    )

    async def prepare(*args, **kwargs):
        return plan

    async def append_event(*args, **kwargs):
        return SimpleNamespace(id=50)

    async def append_line(*args, **kwargs):
        return SimpleNamespace(id=60)

    layers = []

    async def append_layer(*args, **kwargs):
        layers.append(
            kwargs
        )
        return object()

    monkeypatch.setattr(
        executor,
        "prepare_warehouse_transfer_reconciliation",
        prepare,
    )
    monkeypatch.setattr(
        executor,
        "append_warehouse_transfer_event",
        append_event,
    )
    monkeypatch.setattr(
        executor,
        "append_warehouse_transfer_line",
        append_line,
    )
    monkeypatch.setattr(
        executor,
        "append_warehouse_transfer_valuation_layer",
        append_layer,
    )

    await executor.execute_warehouse_transfer_reconciliation(
        object(),
        factory=FakeFactory(
            physical
        ),
        company_id=1,
        history_key=KEY,
        target=ma_target,
        adjustment_date=None,
        created_by=7,
    )

    assert len(layers) == 1
    assert (
        layers[0]["valuation_amount"]
        == Decimal("10.00000000")
    )


@pytest.fixture(autouse=True)
def isolate_landed_cost_boundary(monkeypatch):
    """This module tests the original service body; boundary has separate tests."""
    from unittest.mock import AsyncMock
    import app.services.landed_cost_inventory_lifecycle as boundary
    monkeypatch.setattr(boundary, "lock_landed_cost_company", AsyncMock())
    monkeypatch.setattr(boundary, "reconcile_landed_cost_valuation", AsyncMock())
