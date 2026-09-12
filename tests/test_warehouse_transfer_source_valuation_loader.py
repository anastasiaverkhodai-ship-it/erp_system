from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.company import InventoryValuationMethod
from app.services.warehouse_transfer_source_valuation_loader import (
    WarehouseTransferSourceValuationLoaderError,
    load_warehouse_transfer_source_valuation,
)


class ScalarResult:
    def __init__(
        self,
        *,
        one=None,
        many=(),
    ):
        self._one = one
        self._many = tuple(many)

    def scalar_one_or_none(self):
        return self._one

    def scalars(self):
        return self

    def all(self):
        return list(
            self._many
        )


class FakeDb:
    def __init__(
        self,
        results,
    ):
        self.results = list(
            results
        )
        self.statements = []

    async def execute(
        self,
        statement,
    ):
        self.statements.append(
            statement
        )

        if not self.results:
            raise AssertionError(
                "Unexpected db.execute()"
            )

        return self.results.pop(0)


def fifo_ice():
    return SimpleNamespace(
        id=70,
        company_id=1,
        document_id=30,
        document_line_id=31,
        valuation_method=(
            InventoryValuationMethod.FIFO
        ),
        quantity=Decimal("5.0000"),
        unit_cost=Decimal("11.60000000"),
        valuation_amount=Decimal("58.00000000"),
    )


def ma_ice():
    return SimpleNamespace(
        id=71,
        company_id=1,
        document_id=30,
        document_line_id=31,
        valuation_method=(
            InventoryValuationMethod
            .WEIGHTED_AVERAGE_MOVING
        ),
        quantity=Decimal("3.0000"),
        unit_cost=Decimal("3.33333333"),
        valuation_amount=Decimal("10.00000000"),
    )


def fifo_consumptions():
    return (
        SimpleNamespace(
            id=80,
            company_id=1,
            issue_document_id=30,
            issue_document_line_id=31,
            stock_lot_id=1000,
            quantity=Decimal("3.0000"),
            unit_cost=Decimal("10.0000"),
        ),
        SimpleNamespace(
            id=81,
            company_id=1,
            issue_document_id=30,
            issue_document_line_id=31,
            stock_lot_id=1001,
            quantity=Decimal("2.0000"),
            unit_cost=Decimal("14.0000"),
        ),
    )


@pytest.mark.asyncio
async def test_fifo_loader_returns_exact_two_layers():
    db = FakeDb(
        (
            ScalarResult(
                one=fifo_ice(),
            ),
            ScalarResult(
                many=fifo_consumptions(),
            ),
        )
    )

    result = await load_warehouse_transfer_source_valuation(
        db,
        company_id=1,
        issue_document_id=30,
        issue_document_line_id=31,
    )

    assert len(
        db.statements
    ) == 2

    assert len(
        result.layers
    ) == 2

    assert [
        (
            layer.source_stock_lot_consumption_id,
            layer.quantity,
            layer.unit_cost,
            layer.valuation_amount,
        )
        for layer in result.layers
    ] == [
        (
            80,
            Decimal("3.0000"),
            Decimal("10.0000"),
            Decimal("30.00000000"),
        ),
        (
            81,
            Decimal("2.0000"),
            Decimal("14.0000"),
            Decimal("28.00000000"),
        ),
    ]


@pytest.mark.asyncio
async def test_fifo_loader_preserves_quantity_and_value():
    db = FakeDb(
        (
            ScalarResult(
                one=fifo_ice(),
            ),
            ScalarResult(
                many=fifo_consumptions(),
            ),
        )
    )

    result = await load_warehouse_transfer_source_valuation(
        db,
        company_id=1,
        issue_document_id=30,
        issue_document_line_id=31,
    )

    assert (
        result.layer_quantity
        == Decimal("5.0000")
    )

    assert (
        result.layer_valuation_amount
        == Decimal("58.00000000")
    )


@pytest.mark.asyncio
async def test_ma_loader_uses_only_source_ice():
    db = FakeDb(
        (
            ScalarResult(
                one=ma_ice(),
            ),
        )
    )

    result = await load_warehouse_transfer_source_valuation(
        db,
        company_id=1,
        issue_document_id=30,
        issue_document_line_id=31,
    )

    # ICE query only. No FIFO query.
    assert len(
        db.statements
    ) == 1

    assert len(
        result.layers
    ) == 1

    layer = result.layers[0]

    assert layer.quantity == Decimal(
        "3.0000"
    )

    assert (
        layer.unit_cost
        == Decimal("3.33333333")
    )

    assert (
        layer.valuation_amount
        == Decimal("10.00000000")
    )

    assert (
        layer.source_stock_lot_consumption_id
        is None
    )


@pytest.mark.asyncio
async def test_ma_loader_exposes_4dp_receipt_precision_loss():
    db = FakeDb(
        (
            ScalarResult(
                one=ma_ice(),
            ),
        )
    )

    result = await load_warehouse_transfer_source_valuation(
        db,
        company_id=1,
        issue_document_id=30,
        issue_document_line_id=31,
    )

    assert (
        result.requires_exact_receipt_value_path
        is True
    )


@pytest.mark.asyncio
async def test_missing_ice_rejected():
    db = FakeDb(
        (
            ScalarResult(
                one=None,
            ),
        )
    )

    with pytest.raises(
        WarehouseTransferSourceValuationLoaderError,
        match="InventoryCostEntry was not found",
    ):
        await load_warehouse_transfer_source_valuation(
            db,
            company_id=1,
            issue_document_id=30,
            issue_document_line_id=31,
        )


@pytest.mark.asyncio
async def test_fifo_without_consumptions_rejected():
    db = FakeDb(
        (
            ScalarResult(
                one=fifo_ice(),
            ),
            ScalarResult(
                many=(),
            ),
        )
    )

    with pytest.raises(
        WarehouseTransferSourceValuationLoaderError,
        match="no StockLotConsumption",
    ):
        await load_warehouse_transfer_source_valuation(
            db,
            company_id=1,
            issue_document_id=30,
            issue_document_line_id=31,
        )


@pytest.mark.asyncio
async def test_fifo_loader_rejects_value_mismatch():
    bad = (
        SimpleNamespace(
            id=80,
            quantity=Decimal("3"),
            unit_cost=Decimal("10"),
        ),
        SimpleNamespace(
            id=81,
            quantity=Decimal("2"),
            unit_cost=Decimal("15"),
        ),
    )

    db = FakeDb(
        (
            ScalarResult(
                one=fifo_ice(),
            ),
            ScalarResult(
                many=bad,
            ),
        )
    )

    with pytest.raises(
        WarehouseTransferSourceValuationLoaderError,
        match="valuation does not equal",
    ):
        await load_warehouse_transfer_source_valuation(
            db,
            company_id=1,
            issue_document_id=30,
            issue_document_line_id=31,
        )


@pytest.mark.parametrize(
    "field",
    (
        "company_id",
        "issue_document_id",
        "issue_document_line_id",
    ),
)
@pytest.mark.asyncio
async def test_bool_id_rejected(
    field,
):
    values = {
        "company_id": 1,
        "issue_document_id": 30,
        "issue_document_line_id": 31,
    }

    values[field] = True

    db = FakeDb(
        ()
    )

    with pytest.raises(
        WarehouseTransferSourceValuationLoaderError,
        match="positive integer",
    ):
        await load_warehouse_transfer_source_valuation(
            db,
            **values,
        )

    assert db.statements == []
