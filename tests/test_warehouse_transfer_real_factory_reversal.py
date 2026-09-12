from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.services.warehouse_transfer_physical_factory as factory_service


class OneResult:
    def __init__(
        self,
        value,
    ):
        self.value = value

    def scalar_one_or_none(
        self,
    ):
        return self.value


class ScalarList:
    def __init__(
        self,
        values,
    ):
        self.values = values

    def all(
        self,
    ):
        return list(
            self.values
        )


class ManyResult:
    def __init__(
        self,
        values,
    ):
        self.values = values

    def scalars(
        self,
    ):
        return ScalarList(
            self.values
        )


class FakeDb:
    def __init__(
        self,
        *,
        event,
        lines,
        layers,
    ):
        self.results = (
            OneResult(event),
            ManyResult(lines),
            ManyResult(layers),
        )
        self.index = 0
        self.flush_calls = 0

    async def execute(
        self,
        statement,
    ):
        if self.index >= len(
            self.results
        ):
            raise AssertionError(
                "unexpected execute call"
            )

        result = self.results[
            self.index
        ]

        self.index += 1

        return result

    async def flush(
        self,
    ):
        self.flush_calls += 1


def make_original():
    return SimpleNamespace(
        id=100,
        target=SimpleNamespace(
            company_id=1,
            source_warehouse_id=10,
            destination_warehouse_id=20,
            transfer_date=date(
                2026,
                9,
                10,
            ),
        ),
    )


def make_event():
    return SimpleNamespace(
        id=100,
        company_id=1,
        source_warehouse_id=10,
        destination_warehouse_id=20,
        transfer_date=date(
            2026,
            9,
            10,
        ),
        reversal_of_id=None,
    )


def make_lines():
    return (
        SimpleNamespace(
            id=201,
            company_id=1,
            product_id=301,
            source_warehouse_id=10,
            destination_warehouse_id=20,
            quantity=Decimal("5"),
            issue_document_id=401,
            issue_document_line_id=501,
            receipt_document_id=402,
        ),
    )


def make_layers():
    return (
        SimpleNamespace(
            id=601,
            company_id=1,
            transfer_line_id=201,
            product_id=301,
            destination_warehouse_id=20,
            quantity=Decimal("3"),
            source_stock_lot_consumption_id=701,
            destination_receipt_document_id=402,
            destination_receipt_document_line_id=801,
        ),
        SimpleNamespace(
            id=602,
            company_id=1,
            transfer_line_id=201,
            product_id=301,
            destination_warehouse_id=20,
            quantity=Decimal("2"),
            source_stock_lot_consumption_id=702,
            destination_receipt_document_id=402,
            destination_receipt_document_line_id=802,
        ),
    )


@pytest.mark.asyncio
async def test_destination_reversed_before_source(
    monkeypatch,
):
    chronology = []

    async def fake_reverse(
        db,
        *,
        company_id,
        document_id,
        reversal_date,
        reversed_by,
    ):
        chronology.append(
            document_id
        )

    import app.services.warehouse_transfer_posting_service as posting

    monkeypatch.setattr(
        posting,
        "reverse_warehouse_transfer_document",
        fake_reverse,
    )

    db = FakeDb(
        event=make_event(),
        lines=make_lines(),
        layers=make_layers(),
    )

    await (
        factory_service
        .DefaultWarehouseTransferPhysicalFactory()
        .reverse_transfer(
            db,
            original_event=make_original(),
            reversal_date=date(
                2026,
                9,
                11,
            ),
            created_by=9,
        )
    )

    assert chronology == [
        402,
        401,
    ]

    assert db.flush_calls == 1


@pytest.mark.asyncio
async def test_source_not_reversed_when_destination_fails(
    monkeypatch,
):
    chronology = []

    import app.services.warehouse_transfer_posting_service as posting

    async def fake_reverse(
        db,
        *,
        company_id,
        document_id,
        reversal_date,
        reversed_by,
    ):
        chronology.append(
            document_id
        )

        if document_id == 402:
            raise posting.WarehouseTransferPostingError(
                "destination cannot be reversed"
            )

    monkeypatch.setattr(
        posting,
        "reverse_warehouse_transfer_document",
        fake_reverse,
    )

    db = FakeDb(
        event=make_event(),
        lines=make_lines(),
        layers=make_layers(),
    )

    with pytest.raises(
        ValueError,
        match="destination transfer RECEIPT reversal failed",
    ):
        await (
            factory_service
            .DefaultWarehouseTransferPhysicalFactory()
            .reverse_transfer(
                db,
                original_event=make_original(),
                reversal_date=date(
                    2026,
                    9,
                    11,
                ),
                created_by=9,
            )
        )

    assert chronology == [
        402,
    ]


@pytest.mark.asyncio
async def test_corrupt_layer_quantity_blocks_physical_reversal(
    monkeypatch,
):
    called = False

    import app.services.warehouse_transfer_posting_service as posting

    async def fake_reverse(
        *args,
        **kwargs,
    ):
        nonlocal called
        called = True

    monkeypatch.setattr(
        posting,
        "reverse_warehouse_transfer_document",
        fake_reverse,
    )

    bad_layers = (
        SimpleNamespace(
            id=601,
            company_id=1,
            transfer_line_id=201,
            product_id=301,
            destination_warehouse_id=20,
            quantity=Decimal("4"),
            source_stock_lot_consumption_id=701,
            destination_receipt_document_id=402,
            destination_receipt_document_line_id=801,
        ),
    )

    db = FakeDb(
        event=make_event(),
        lines=make_lines(),
        layers=bad_layers,
    )

    with pytest.raises(
        ValueError,
        match="does not equal business transfer quantity",
    ):
        await (
            factory_service
            .DefaultWarehouseTransferPhysicalFactory()
            .reverse_transfer(
                db,
                original_event=make_original(),
                reversal_date=date(
                    2026,
                    9,
                    11,
                ),
                created_by=9,
            )
        )

    assert called is False
