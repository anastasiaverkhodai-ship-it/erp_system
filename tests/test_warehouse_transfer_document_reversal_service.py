from datetime import date
from types import SimpleNamespace

import pytest

from app.models.document import (
    DocumentStatus,
    DocumentType,
)

import app.services.warehouse_transfer_posting_service as service


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


class ScalarCollection:
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
        return ScalarCollection(
            self.values
        )


class FakeDb:
    def __init__(
        self,
        *,
        document,
        movements,
    ):
        self.document = document
        self.movements = movements
        self.execute_calls = 0
        self.flush_calls = 0

    async def execute(
        self,
        statement,
    ):
        self.execute_calls += 1

        if self.execute_calls == 1:
            return OneResult(
                self.document
            )

        if self.execute_calls == 2:
            return ManyResult(
                self.movements
            )

        raise AssertionError(
            "unexpected db.execute"
        )

    async def flush(
        self,
    ):
        self.flush_calls += 1


def make_document(
    *,
    status=DocumentStatus.POSTED,
    document_type=DocumentType.RECEIPT,
):
    return SimpleNamespace(
        id=10,
        company_id=1,
        document_type=document_type,
        status=status,
        lines=[],
        reversed_at=None,
        reversed_by=None,
    )


def make_movement():
    return SimpleNamespace(
        id=1,
        company_id=1,
        document_id=10,
    )


@pytest.mark.asyncio
async def test_reversal_uses_warehouse_handler_only(
    monkeypatch,
):
    document = make_document()
    movement = make_movement()

    db = FakeDb(
        document=document,
        movements=(
            movement,
        ),
    )

    chronology = []

    async def fake_period(
        **kwargs,
    ):
        chronology.append(
            "period"
        )

    class FakeContext:
        reversal_time = "REVERSAL_TIME"
        reversed_by = 77
        original_stock_movements = ()
        original_journal_entry = object()

    def fake_context(
        **kwargs,
    ):
        chronology.append(
            "context"
        )
        return FakeContext()

    class FakeHandler:
        async def reverse(
            self,
            context,
        ):
            chronology.append(
                "warehouse"
            )

            assert (
                context.original_stock_movements
                == (movement,)
            )

            assert (
                context.original_journal_entry
                is None
            )

    import app.services.accounting_period_service as period_module
    import app.services.reversal_context as context_module
    import app.services.warehouse_reversal_handler as handler_module

    monkeypatch.setattr(
        period_module,
        "ensure_period_open",
        fake_period,
    )

    monkeypatch.setattr(
        context_module,
        "create_reversal_context",
        fake_context,
    )

    monkeypatch.setattr(
        handler_module,
        "WarehouseReversalHandler",
        FakeHandler,
    )

    result = (
        await service.reverse_warehouse_transfer_document(
            db,
            company_id=1,
            document_id=10,
            reversal_date=date(
                2026,
                9,
                11,
            ),
            reversed_by=77,
        )
    )

    assert result is document

    assert chronology == [
        "period",
        "context",
        "warehouse",
    ]

    assert (
        document.status
        == DocumentStatus.REVERSED
    )

    assert (
        document.reversed_at
        == "REVERSAL_TIME"
    )

    assert (
        document.reversed_by
        == 77
    )

    assert (
        db.flush_calls
        == 1
    )


@pytest.mark.asyncio
async def test_non_posted_rejected():
    document = make_document(
        status=DocumentStatus.REVERSED
    )

    db = FakeDb(
        document=document,
        movements=(),
    )

    with pytest.raises(
        service.WarehouseTransferPostingError,
        match="only posted",
    ):
        await service.reverse_warehouse_transfer_document(
            db,
            company_id=1,
            document_id=10,
            reversal_date=date(
                2026,
                9,
                11,
            ),
            reversed_by=77,
        )


@pytest.mark.asyncio
async def test_adjustment_rejected():
    document = make_document(
        document_type=DocumentType.ADJUSTMENT
    )

    db = FakeDb(
        document=document,
        movements=(),
    )

    with pytest.raises(
        service.WarehouseTransferPostingError,
        match="ISSUE or RECEIPT",
    ):
        await service.reverse_warehouse_transfer_document(
            db,
            company_id=1,
            document_id=10,
            reversal_date=date(
                2026,
                9,
                11,
            ),
            reversed_by=77,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("company_id", 0),
        ("company_id", True),
        ("document_id", 0),
        ("document_id", False),
        ("reversed_by", 0),
        ("reversed_by", True),
    ),
)
async def test_positive_ids_required(
    field,
    value,
):
    kwargs = {
        "company_id": 1,
        "document_id": 10,
        "reversal_date": date(
            2026,
            9,
            11,
        ),
        "reversed_by": 77,
    }

    kwargs[field] = value

    with pytest.raises(
        service.WarehouseTransferPostingError
    ):
        await service.reverse_warehouse_transfer_document(
            FakeDb(
                document=None,
                movements=(),
            ),
            **kwargs,
        )
