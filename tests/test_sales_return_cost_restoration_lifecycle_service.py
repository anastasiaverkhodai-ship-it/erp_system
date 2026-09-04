from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.sales_return_cost_restoration_lifecycle_service as service

from app.models.document import (
    DocumentType,
)
from app.services.sales_return_cost_restoration_lifecycle_service import (
    SalesReturnCostRestorationLifecycleError,
    SalesReturnCostRestorationLifecycleSourceError,
    _apply_created_sales_return_cost_restoration_events,
    _load_sales_return_cost_runtime_context,
    reconcile_sales_return_cost_restoration_lifecycle_for_fulfillment_line,
)
from app.services.sales_return_stock_restoration_service import (
    SalesReturnStockRestorationError,
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


def cost_event(
    *,
    event_id,
    reversal_of_id=None,
):
    return SimpleNamespace(
        id=event_id,
        company_id=1,
        trade_return_event_id=100,
        restoration_date=D2,
        reversal_of_id=(
            reversal_of_id
        ),
    )


def runtime_context():
    return SimpleNamespace(
        trade_return_event=SimpleNamespace(
            id=100
        ),
        document=SimpleNamespace(
            id=200
        ),
        line=SimpleNamespace(
            id=201
        ),
        fifo_slices=(
            SimpleNamespace(
                id=300
            ),
        ),
    )


@pytest.mark.asyncio
async def test_exact_reversal_then_replacement_runtime_order(
    monkeypatch,
):
    reversal = cost_event(
        event_id=11,
        reversal_of_id=10,
    )

    replacement = cost_event(
        event_id=12,
        reversal_of_id=None,
    )

    result = SimpleNamespace(
        created_events=(
            reversal,
            replacement,
        )
    )

    context = runtime_context()

    monkeypatch.setattr(
        service,
        "_load_sales_return_cost_runtime_context",
        AsyncMock(
            return_value=context
        ),
    )

    calls = []

    async def reverse_physical(
        db,
        *,
        document,
        line,
        reversal_event,
    ):
        calls.append(
            (
                "physical_reverse",
                reversal_event.id,
            )
        )

    async def reverse_gl(
        db,
        *,
        reversal_event,
        reversed_by,
    ):
        calls.append(
            (
                "gl_reverse",
                reversal_event.id,
            )
        )

    async def restore_physical(
        db,
        *,
        document,
        line,
        trade_return_event,
        cost_event,
        fifo_slices,
    ):
        calls.append(
            (
                "physical_restore",
                cost_event.id,
            )
        )

    async def post_gl(
        db,
        *,
        event,
        created_by,
    ):
        calls.append(
            (
                "gl_original",
                event.id,
            )
        )

    monkeypatch.setattr(
        service,
        "reverse_sales_return_physical_cost_state",
        reverse_physical,
    )

    monkeypatch.setattr(
        service,
        "reverse_sales_return_cost_restoration_journal_entry",
        reverse_gl,
    )

    monkeypatch.setattr(
        service,
        "restore_sales_return_physical_cost_state",
        restore_physical,
    )

    monkeypatch.setattr(
        service,
        "generate_and_post_sales_return_cost_restoration_journal_entry",
        post_gl,
    )

    await _apply_created_sales_return_cost_restoration_events(
        object(),
        company_id=1,
        result=result,
        created_by=7,
    )

    assert calls == [
        (
            "physical_reverse",
            11,
        ),
        (
            "gl_reverse",
            11,
        ),
        (
            "physical_restore",
            12,
        ),
        (
            "gl_original",
            12,
        ),
    ]


@pytest.mark.asyncio
async def test_original_event_restores_physical_before_gl(
    monkeypatch,
):
    original = cost_event(
        event_id=12
    )

    result = SimpleNamespace(
        created_events=(
            original,
        )
    )

    context = runtime_context()

    monkeypatch.setattr(
        service,
        "_load_sales_return_cost_runtime_context",
        AsyncMock(
            return_value=context
        ),
    )

    calls = []

    async def restore(
        *args,
        **kwargs,
    ):
        calls.append(
            "physical"
        )

    async def post(
        *args,
        **kwargs,
    ):
        calls.append(
            "gl"
        )

    monkeypatch.setattr(
        service,
        "restore_sales_return_physical_cost_state",
        restore,
    )

    monkeypatch.setattr(
        service,
        "generate_and_post_sales_return_cost_restoration_journal_entry",
        post,
    )

    await _apply_created_sales_return_cost_restoration_events(
        object(),
        company_id=1,
        result=result,
        created_by=7,
    )

    assert calls == [
        "physical",
        "gl",
    ]


@pytest.mark.asyncio
async def test_no_created_events_is_noop(
    monkeypatch,
):
    result = SimpleNamespace(
        created_events=()
    )

    loader = AsyncMock()

    monkeypatch.setattr(
        service,
        "_load_sales_return_cost_runtime_context",
        loader,
    )

    await _apply_created_sales_return_cost_restoration_events(
        object(),
        company_id=1,
        result=result,
        created_by=7,
    )

    loader.assert_not_awaited()


class ScalarResult:
    def __init__(
        self,
        value,
    ):
        self.value = value

    def scalar_one_or_none(
        self,
    ):
        return self.value


class ScalarSequence:
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


class ContextDB:
    def __init__(
        self,
        *,
        execute_values,
        fifo_values=(),
    ):
        self.execute_values = list(
            execute_values
        )

        self.fifo_values = tuple(
            fifo_values
        )

    async def execute(
        self,
        statement,
    ):
        if not self.execute_values:
            raise AssertionError(
                "Unexpected execute()"
            )

        return ScalarResult(
            self.execute_values.pop(
                0
            )
        )

    async def scalars(
        self,
        statement,
    ):
        return ScalarSequence(
            self.fifo_values
        )


@pytest.mark.asyncio
async def test_runtime_context_loads_exact_return_document_line_and_fifo():
    trade_return = SimpleNamespace(
        id=100,
        company_id=1,
        direction="sale",
        return_document_id=200,
        return_document_type="receipt",
        return_document_line_id=201,
        product_id=10,
        return_warehouse_id=20,
    )

    document = SimpleNamespace(
        id=200,
        company_id=1,
        document_type=(
            DocumentType.RECEIPT
        ),
    )

    line = SimpleNamespace(
        id=201,
        document_id=200,
        product_id=10,
        warehouse_id=20,
    )

    fifo = SimpleNamespace(
        id=300
    )

    db = ContextDB(
        execute_values=(
            trade_return,
            document,
            line,
        ),
        fifo_values=(
            fifo,
        ),
    )

    result = (
        await _load_sales_return_cost_runtime_context(
            db,
            company_id=1,
            event=cost_event(
                event_id=12
            ),
        )
    )

    assert (
        result.trade_return_event
        is trade_return
    )

    assert result.document is document

    assert result.line is line

    assert result.fifo_slices == (
        fifo,
    )


@pytest.mark.asyncio
async def test_runtime_context_missing_trade_return_is_error():
    db = ContextDB(
        execute_values=(
            None,
        )
    )

    with pytest.raises(
        SalesReturnCostRestorationLifecycleSourceError,
        match="TradeReturnEvent",
    ):
        await _load_sales_return_cost_runtime_context(
            db,
            company_id=1,
            event=cost_event(
                event_id=12
            ),
        )


@pytest.mark.asyncio
async def test_runtime_context_rejects_non_sale_source():
    trade_return = SimpleNamespace(
        id=100,
        company_id=1,
        direction="purchase",
        return_document_id=200,
        return_document_type="issue",
        return_document_line_id=201,
        product_id=10,
        return_warehouse_id=20,
    )

    db = ContextDB(
        execute_values=(
            trade_return,
        )
    )

    with pytest.raises(
        SalesReturnCostRestorationLifecycleSourceError,
        match="sales TradeReturnEvent",
    ):
        await _load_sales_return_cost_runtime_context(
            db,
            company_id=1,
            event=cost_event(
                event_id=12
            ),
        )


@pytest.mark.asyncio
async def test_main_lifecycle_reconcile_then_apply(
    monkeypatch,
):
    expected = SimpleNamespace(
        created_events=()
    )

    reconcile = AsyncMock(
        return_value=expected
    )

    apply = AsyncMock()

    monkeypatch.setattr(
        service,
        "reconcile_sales_return_cost_restoration_for_fulfillment_line",
        reconcile,
    )

    monkeypatch.setattr(
        service,
        "_apply_created_sales_return_cost_restoration_events",
        apply,
    )

    result = (
        await reconcile_sales_return_cost_restoration_lifecycle_for_fulfillment_line(
            object(),
            company_id=1,
            fulfillment_id=2,
            fulfillment_line_id=3,
            adjustment_date=D2,
            created_by=7,
        )
    )

    assert result is expected

    reconcile.assert_awaited_once_with(
        object() if False else reconcile.await_args.args[0],
        company_id=1,
        fulfillment_id=2,
        fulfillment_line_id=3,
        created_by=7,
        adjustment_date=D2,
    )

    apply.assert_awaited_once()

    assert (
        apply.await_args.kwargs[
            "result"
        ]
        is expected
    )

    assert (
        apply.await_args.kwargs[
            "created_by"
        ]
        == 7
    )


@pytest.mark.asyncio
async def test_main_lifecycle_wraps_physical_runtime_error(
    monkeypatch,
):
    expected = SimpleNamespace(
        created_events=()
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_return_cost_restoration_for_fulfillment_line",
        AsyncMock(
            return_value=expected
        ),
    )

    monkeypatch.setattr(
        service,
        "_apply_created_sales_return_cost_restoration_events",
        AsyncMock(
            side_effect=(
                SalesReturnStockRestorationError(
                    "physical failure"
                )
            )
        ),
    )

    with pytest.raises(
        SalesReturnCostRestorationLifecycleError,
        match="runtime application failed",
    ):
        await reconcile_sales_return_cost_restoration_lifecycle_for_fulfillment_line(
            object(),
            company_id=1,
            fulfillment_id=2,
            fulfillment_line_id=3,
            adjustment_date=D2,
            created_by=7,
        )


@pytest.mark.parametrize(
    (
        "field",
        "value",
    ),
    (
        (
            "company_id",
            0,
        ),
        (
            "fulfillment_id",
            0,
        ),
        (
            "fulfillment_line_id",
            0,
        ),
        (
            "created_by",
            0,
        ),
    ),
)
@pytest.mark.asyncio
async def test_main_lifecycle_validates_positive_ids(
    field,
    value,
):
    kwargs = {
        "company_id": 1,
        "fulfillment_id": 2,
        "fulfillment_line_id": 3,
        "adjustment_date": D2,
        "created_by": 7,
    }

    kwargs[
        field
    ] = value

    with pytest.raises(
        ValueError
    ):
        await reconcile_sales_return_cost_restoration_lifecycle_for_fulfillment_line(
            object(),
            **kwargs,
        )
