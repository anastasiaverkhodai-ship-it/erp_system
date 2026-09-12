from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.company import (
    InventoryValuationMethod,
)
from app.models.document import (
    DocumentStatus,
    DocumentType,
)
from app.models.document_line import DocumentLine
import app.services.warehouse_transfer_physical_factory as service


class FakeDb:
    def __init__(self):
        self.next_id = 100
        self.flush_calls = 0
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flush_calls += 1

        # Model SQLAlchemy save-update cascade:
        # persistent Document -> appended DocumentLine children.
        objects = list(
            self.added
        )

        index = 0

        while index < len(objects):
            obj = objects[index]
            index += 1

            if getattr(
                obj,
                "id",
                None,
            ) is None:
                try:
                    obj.id = self.next_id
                    self.next_id += 1
                except Exception:
                    pass

            children = getattr(
                obj,
                "lines",
                None,
            )

            if children:
                for child in children:
                    if child not in objects:
                        objects.append(
                            child
                        )


def target():
    return SimpleNamespace(
        company_id=1,
        source_warehouse_id=10,
        destination_warehouse_id=20,
        transfer_date=date(
            2026,
            9,
            10,
        ),
        lines=(
            SimpleNamespace(
                product_id=101,
                quantity=Decimal("5"),
            ),
        ),
    )


def fifo_valuation():
    return SimpleNamespace(
        valuation_method=(
            InventoryValuationMethod.FIFO
        ),
        layers=(
            SimpleNamespace(
                valuation_method=(
                    InventoryValuationMethod.FIFO
                ),
                quantity=Decimal("3"),
                unit_cost=Decimal("10"),
                valuation_amount=Decimal("30"),
                source_inventory_cost_entry_id=501,
                source_stock_lot_consumption_id=601,
            ),
            SimpleNamespace(
                valuation_method=(
                    InventoryValuationMethod.FIFO
                ),
                quantity=Decimal("2"),
                unit_cost=Decimal("14"),
                valuation_amount=Decimal("28"),
                source_inventory_cost_entry_id=501,
                source_stock_lot_consumption_id=602,
            ),
        ),
    )


def ma_valuation():
    return SimpleNamespace(
        valuation_method=(
            InventoryValuationMethod.WEIGHTED_AVERAGE_MOVING
        ),
        layers=(
            SimpleNamespace(
                valuation_method=(
                    InventoryValuationMethod.WEIGHTED_AVERAGE_MOVING
                ),
                quantity=Decimal("5"),
                unit_cost=Decimal("11.60000000"),
                valuation_amount=Decimal("58.00000000"),
                source_inventory_cost_entry_id=701,
                source_stock_lot_consumption_id=None,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_fifo_creates_two_destination_receipt_layers(
    monkeypatch,
):
    db = FakeDb()
    posting_calls = []

    async def fake_post(
        db,
        *,
        document,
        created_by,
        exact_receipt_valuation_amounts=None,
    ):
        posting_calls.append(
            (
                document.document_type,
                exact_receipt_valuation_amounts,
                tuple(
                obj
                for obj in db.added
                if (
                    isinstance(obj, DocumentLine)
                    and obj.document_id
                    == document.id
                )
            ),
            )
        )
        document.status = (
            DocumentStatus.POSTED
        )

    async def fake_load(db, **kwargs):
        return fifo_valuation()

    monkeypatch.setattr(
        service,
        "post_warehouse_transfer_document",
        fake_post,
        raising=False,
    )

    # Imports occur inside method, so patch source modules.
    import app.services.warehouse_transfer_posting_service as poster
    import app.services.warehouse_transfer_source_valuation_loader as loader

    monkeypatch.setattr(
        poster,
        "post_warehouse_transfer_document",
        fake_post,
    )

    monkeypatch.setattr(
        loader,
        "load_warehouse_transfer_source_valuation",
        fake_load,
    )

    factory = (
        service.DefaultWarehouseTransferPhysicalFactory()
    )

    result = await factory.create_transfer(
        db,
        target=target(),
        created_by=9,
    )

    assert len(result.lines) == 1

    line = result.lines[0]

    assert line.quantity == Decimal("5")
    assert len(line.valuation_layers) == 2

    assert [
        layer.quantity
        for layer in line.valuation_layers
    ] == [
        Decimal("3"),
        Decimal("2"),
    ]

    assert [
        layer.unit_cost
        for layer in line.valuation_layers
    ] == [
        Decimal("10"),
        Decimal("14"),
    ]

    assert [
        layer.source_stock_lot_consumption_id
        for layer in line.valuation_layers
    ] == [
        601,
        602,
    ]

    assert (
        posting_calls[0][0]
        == DocumentType.ISSUE
    )

    assert (
        posting_calls[1][0]
        == DocumentType.RECEIPT
    )

    assert (
        posting_calls[1][1]
        is None
    )

    receipt_lines = posting_calls[1][2]

    assert len(receipt_lines) == 2

    assert [
        row.quantity
        for row in receipt_lines
    ] == [
        Decimal("3"),
        Decimal("2"),
    ]

    assert [
        row.price
        for row in receipt_lines
    ] == [
        Decimal("10"),
        Decimal("14"),
    ]


@pytest.mark.asyncio
async def test_ma_uses_one_receipt_line_and_exact_value(
    monkeypatch,
):
    db = FakeDb()
    posting_calls = []

    async def fake_post(
        db,
        *,
        document,
        created_by,
        exact_receipt_valuation_amounts=None,
    ):
        posting_calls.append(
            (
                document.document_type,
                exact_receipt_valuation_amounts,
                tuple(
                obj
                for obj in db.added
                if (
                    isinstance(obj, DocumentLine)
                    and obj.document_id
                    == document.id
                )
            ),
            )
        )
        document.status = (
            DocumentStatus.POSTED
        )

    async def fake_load(db, **kwargs):
        return ma_valuation()

    import app.services.warehouse_transfer_posting_service as poster
    import app.services.warehouse_transfer_source_valuation_loader as loader

    monkeypatch.setattr(
        poster,
        "post_warehouse_transfer_document",
        fake_post,
    )

    monkeypatch.setattr(
        loader,
        "load_warehouse_transfer_source_valuation",
        fake_load,
    )

    factory = (
        service.DefaultWarehouseTransferPhysicalFactory()
    )

    result = await factory.create_transfer(
        db,
        target=target(),
        created_by=9,
    )

    assert len(result.lines) == 1
    assert len(
        result.lines[0].valuation_layers
    ) == 1

    receipt_call = posting_calls[1]

    receipt_lines = (
        receipt_call[2]
    )

    assert len(receipt_lines) == 1

    receipt_line = (
        receipt_lines[0]
    )

    assert (
        receipt_line.price
        == Decimal("11.6000")
    )

    assert receipt_call[1] == {
        receipt_line.id: Decimal(
            "58.00000000"
        )
    }


@pytest.mark.asyncio
async def test_source_issue_is_posted_before_valuation_load(
    monkeypatch,
):
    db = FakeDb()
    chronology = []

    async def fake_post(
        db,
        *,
        document,
        created_by,
        exact_receipt_valuation_amounts=None,
    ):
        chronology.append(
            document.document_type.value
        )

    async def fake_load(db, **kwargs):
        chronology.append(
            "load_source_valuation"
        )
        return ma_valuation()

    import app.services.warehouse_transfer_posting_service as poster
    import app.services.warehouse_transfer_source_valuation_loader as loader

    monkeypatch.setattr(
        poster,
        "post_warehouse_transfer_document",
        fake_post,
    )

    monkeypatch.setattr(
        loader,
        "load_warehouse_transfer_source_valuation",
        fake_load,
    )

    await (
        service.DefaultWarehouseTransferPhysicalFactory()
        .create_transfer(
            db,
            target=target(),
            created_by=9,
        )
    )

    assert chronology == [
        "issue",
        "load_source_valuation",
        "receipt",
    ]


@pytest.mark.asyncio
async def test_duplicate_product_rejected():
    value = target()

    value.lines = (
        SimpleNamespace(
            product_id=101,
            quantity=Decimal("1"),
        ),
        SimpleNamespace(
            product_id=101,
            quantity=Decimal("2"),
        ),
    )

    with pytest.raises(
        ValueError,
        match="duplicate product",
    ):
        await (
            service.DefaultWarehouseTransferPhysicalFactory()
            .create_transfer(
                FakeDb(),
                target=value,
                created_by=9,
            )
        )


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_reverse_requires_persisted_original_event():
    with pytest.raises(
        ValueError,
        match=(
            "persisted original transfer "
            "event id is required"
        ),
    ):
        await (
            service.DefaultWarehouseTransferPhysicalFactory()
            .reverse_transfer(
                FakeDb(),
                original_event=object(),
                reversal_date=date(
                    2026,
                    9,
                    11,
                ),
                created_by=9,
            )
        )
