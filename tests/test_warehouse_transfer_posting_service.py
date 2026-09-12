from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.document import (
    DocumentStatus,
    DocumentType,
)

import app.services.warehouse_transfer_posting_service as service


class FakeDb:
    def __init__(self):
        self.flush_calls = 0

    async def flush(self):
        self.flush_calls += 1

    async def refresh(
        self,
        instance,
        attribute_names=None,
    ):
        assert attribute_names == ["lines"]
        assert hasattr(instance, "lines")



def make_document(
    *,
    status=DocumentStatus.DRAFT,
):
    line = SimpleNamespace(
        id=11,
        product_id=20,
        warehouse_id=30,
        quantity=Decimal("3"),
        price=Decimal("3.3333"),
    )

    document = SimpleNamespace(
        id=10,
        company_id=1,
        document_type=DocumentType.RECEIPT,
        document_date=date(
            2026,
            9,
            10,
        ),
        status=status,
        posted_at=None,
        lines=[line],
    )

    return document, line


@pytest.mark.asyncio
async def test_posts_warehouse_only_with_exact_value(
    monkeypatch,
):
    document, line = make_document()
    db = FakeDb()

    calls = []

    async def period(**kwargs):
        calls.append("period")

    class Handler:
        async def post(
            self,
            context,
        ):
            calls.append(
                "warehouse"
            )

            assert (
                context.accounting_rule_id
                == 0
            )

            assert (
                context.get_receipt_exact_valuation_amount(
                    line.id
                )
                == Decimal("10")
            )

    monkeypatch.setattr(
        service,
        "ensure_period_open",
        period,
    )

    monkeypatch.setattr(
        service,
        "WarehousePostingHandler",
        Handler,
    )

    await service.post_warehouse_transfer_document(
        db,
        document=document,
        created_by=7,
        exact_receipt_valuation_amounts={
            line.id: Decimal("10")
        },
    )

    assert calls == [
        "period",
        "warehouse",
    ]

    assert (
        document.status
        == DocumentStatus.POSTED
    )

    assert (
        document.posted_at
        is not None
    )

    assert (
        db.flush_calls
        == 1
    )


@pytest.mark.asyncio
async def test_no_exact_override_is_valid(
    monkeypatch,
):
    document, line = make_document()
    db = FakeDb()

    async def period(**kwargs):
        pass

    class Handler:
        async def post(
            self,
            context,
        ):
            assert (
                context.get_receipt_exact_valuation_amount(
                    line.id
                )
                is None
            )

    monkeypatch.setattr(
        service,
        "ensure_period_open",
        period,
    )

    monkeypatch.setattr(
        service,
        "WarehousePostingHandler",
        Handler,
    )

    await service.post_warehouse_transfer_document(
        db,
        document=document,
        created_by=7,
    )

    assert (
        document.status
        == DocumentStatus.POSTED
    )


@pytest.mark.asyncio
async def test_non_draft_rejected_before_period(
    monkeypatch,
):
    document, _ = make_document(
        status=DocumentStatus.POSTED
    )

    called = False

    async def period(**kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(
        service,
        "ensure_period_open",
        period,
    )

    with pytest.raises(
        service.WarehouseTransferPostingError,
        match="only draft",
    ):
        await service.post_warehouse_transfer_document(
            FakeDb(),
            document=document,
            created_by=7,
        )

    assert called is False


@pytest.mark.asyncio
async def test_unknown_exact_line_rejected():
    document, _ = make_document()

    with pytest.raises(
        service.WarehouseTransferPostingError,
        match="outside",
    ):
        await service.post_warehouse_transfer_document(
            FakeDb(),
            document=document,
            created_by=7,
            exact_receipt_valuation_amounts={
                999: Decimal("10")
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    (
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ),
)
async def test_invalid_exact_value_rejected(
    value,
):
    document, line = make_document()

    with pytest.raises(
        service.WarehouseTransferPostingError
    ):
        await service.post_warehouse_transfer_document(
            FakeDb(),
            document=document,
            created_by=7,
            exact_receipt_valuation_amounts={
                line.id: value
            },
        )


@pytest.mark.asyncio
async def test_persistent_document_required():
    document, _ = make_document()

    document.id = None

    with pytest.raises(
        service.WarehouseTransferPostingError,
        match="persistent",
    ):
        await service.post_warehouse_transfer_document(
            FakeDb(),
            document=document,
            created_by=7,
        )


@pytest.mark.asyncio
async def test_persistent_lines_required():
    document, line = make_document()

    line.id = None

    with pytest.raises(
        service.WarehouseTransferPostingError,
        match="lines must be persistent",
    ):
        await service.post_warehouse_transfer_document(
            FakeDb(),
            document=document,
            created_by=7,
        )
