from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.sales_return_operational_service as service

from app.services.sales_return_cost_restoration_lifecycle_service import (
    SalesReturnCostRestorationLifecycleError,
)
from app.services.sales_return_recognition_lifecycle_service import (
    SalesReturnRecognitionLifecycleError,
)
from app.services.sales_return_warehouse_quantity_service import (
    SalesReturnWarehouseQuantityError,
)


D1 = date(
    2026,
    9,
    1,
)


def event(
    *,
    event_id=100,
    company_id=1,
    direction="sale",
    return_document_type="receipt",
    reversal_of_id=None,
):
    return SimpleNamespace(
        id=event_id,
        company_id=company_id,
        direction=direction,
        original_fulfillment_id=10,
        original_fulfillment_line_id=11,
        return_document_type=(
            return_document_type
        ),
        return_date=D1,
        reversal_of_id=reversal_of_id,
    )


@pytest.mark.asyncio
async def test_exact_operational_order_quantity_economic_cost(
    monkeypatch,
):
    value = event()

    calls = []

    async def quantity(
        db,
        *,
        event,
    ):
        calls.append(
            (
                "quantity",
                event.id,
            )
        )

        return SimpleNamespace(
            id=1
        )

    async def economic(
        db,
        **kwargs,
    ):
        calls.append(
            (
                "economic",
                kwargs[
                    "fulfillment_line_id"
                ],
            )
        )

        return SimpleNamespace(
            created_events=()
        )

    async def cost(
        db,
        **kwargs,
    ):
        calls.append(
            (
                "cost",
                kwargs[
                    "fulfillment_line_id"
                ],
            )
        )

        return SimpleNamespace(
            created_events=()
        )

    monkeypatch.setattr(
        service,
        "apply_sales_return_warehouse_quantity_event",
        quantity,
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_return_recognition_lifecycle_for_fulfillment_line",
        economic,
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_return_cost_restoration_lifecycle_for_fulfillment_line",
        cost,
    )

    result = (
        await service._apply_loaded_sales_return_operational_event(
            object(),
            company_id=1,
            event=value,
            created_by=7,
        )
    )

    assert calls == [
        (
            "quantity",
            100,
        ),
        (
            "economic",
            11,
        ),
        (
            "cost",
            11,
        ),
    ]

    assert (
        result.trade_return_event
        is value
    )


@pytest.mark.asyncio
async def test_same_return_date_drives_economic_and_cost(
    monkeypatch,
):
    value = event()

    monkeypatch.setattr(
        service,
        "apply_sales_return_warehouse_quantity_event",
        AsyncMock(
            return_value=SimpleNamespace(
                id=1
            )
        ),
    )

    economic = AsyncMock(
        return_value=SimpleNamespace(
            created_events=()
        )
    )

    cost = AsyncMock(
        return_value=SimpleNamespace(
            created_events=()
        )
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_return_recognition_lifecycle_for_fulfillment_line",
        economic,
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_return_cost_restoration_lifecycle_for_fulfillment_line",
        cost,
    )

    await service._apply_loaded_sales_return_operational_event(
        object(),
        company_id=1,
        event=value,
        created_by=7,
    )

    assert (
        economic.await_args.kwargs[
            "adjustment_date"
        ]
        == D1
    )

    assert (
        cost.await_args.kwargs[
            "adjustment_date"
        ]
        == D1
    )

    for mock in (
        economic,
        cost,
    ):
        assert (
            mock.await_args.kwargs[
                "company_id"
            ]
            == 1
        )

        assert (
            mock.await_args.kwargs[
                "fulfillment_id"
            ]
            == 10
        )

        assert (
            mock.await_args.kwargs[
                "fulfillment_line_id"
            ]
            == 11
        )

        assert (
            mock.await_args.kwargs[
                "created_by"
            ]
            == 7
        )


@pytest.mark.asyncio
async def test_reversal_uses_same_operational_pipeline(
    monkeypatch,
):
    value = event(
        event_id=101,
        reversal_of_id=100,
    )

    quantity = AsyncMock(
        return_value=SimpleNamespace(
            id=2
        )
    )

    economic = AsyncMock(
        return_value=SimpleNamespace(
            created_events=()
        )
    )

    cost = AsyncMock(
        return_value=SimpleNamespace(
            created_events=()
        )
    )

    monkeypatch.setattr(
        service,
        "apply_sales_return_warehouse_quantity_event",
        quantity,
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_return_recognition_lifecycle_for_fulfillment_line",
        economic,
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_return_cost_restoration_lifecycle_for_fulfillment_line",
        cost,
    )

    await service._apply_loaded_sales_return_operational_event(
        object(),
        company_id=1,
        event=value,
        created_by=7,
    )

    assert (
        quantity.await_args.kwargs[
            "event"
        ]
        is value
    )

    economic.assert_awaited_once()

    cost.assert_awaited_once()


@pytest.mark.asyncio
async def test_quantity_failure_stops_downstream(
    monkeypatch,
):
    value = event()

    monkeypatch.setattr(
        service,
        "apply_sales_return_warehouse_quantity_event",
        AsyncMock(
            side_effect=(
                SalesReturnWarehouseQuantityError(
                    "quantity failed"
                )
            )
        ),
    )

    economic = AsyncMock()
    cost = AsyncMock()

    monkeypatch.setattr(
        service,
        "reconcile_sales_return_recognition_lifecycle_for_fulfillment_line",
        economic,
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_return_cost_restoration_lifecycle_for_fulfillment_line",
        cost,
    )

    with pytest.raises(
        service.SalesReturnOperationalError,
        match="warehouse quantity",
    ):
        await service._apply_loaded_sales_return_operational_event(
            object(),
            company_id=1,
            event=value,
            created_by=7,
        )

    economic.assert_not_awaited()
    cost.assert_not_awaited()


@pytest.mark.asyncio
async def test_economic_failure_stops_cost(
    monkeypatch,
):
    value = event()

    quantity = AsyncMock(
        return_value=SimpleNamespace(
            id=1
        )
    )

    economic = AsyncMock(
        side_effect=(
            SalesReturnRecognitionLifecycleError(
                "economic failed"
            )
        )
    )

    cost = AsyncMock()

    monkeypatch.setattr(
        service,
        "apply_sales_return_warehouse_quantity_event",
        quantity,
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_return_recognition_lifecycle_for_fulfillment_line",
        economic,
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_return_cost_restoration_lifecycle_for_fulfillment_line",
        cost,
    )

    with pytest.raises(
        service.SalesReturnOperationalError,
        match="economic lifecycle",
    ):
        await service._apply_loaded_sales_return_operational_event(
            object(),
            company_id=1,
            event=value,
            created_by=7,
        )

    quantity.assert_awaited_once()
    cost.assert_not_awaited()


@pytest.mark.asyncio
async def test_cost_failure_is_wrapped(
    monkeypatch,
):
    value = event()

    monkeypatch.setattr(
        service,
        "apply_sales_return_warehouse_quantity_event",
        AsyncMock(
            return_value=SimpleNamespace(
                id=1
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_return_recognition_lifecycle_for_fulfillment_line",
        AsyncMock(
            return_value=SimpleNamespace(
                created_events=()
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_return_cost_restoration_lifecycle_for_fulfillment_line",
        AsyncMock(
            side_effect=(
                SalesReturnCostRestorationLifecycleError(
                    "cost failed"
                )
            )
        ),
    )

    with pytest.raises(
        service.SalesReturnOperationalError,
        match="cost \\+ COGS",
    ):
        await service._apply_loaded_sales_return_operational_event(
            object(),
            company_id=1,
            event=value,
            created_by=7,
        )


def test_company_mismatch_is_rejected_before_side_effects():
    value = event(
        company_id=2
    )

    with pytest.raises(
        service.SalesReturnOperationalSourceError,
        match="company mismatch",
    ):
        service._validate_operational_event(
            company_id=1,
            event=value,
            created_by=7,
        )


def test_purchase_return_is_rejected():
    value = event(
        direction="purchase"
    )

    with pytest.raises(
        service.SalesReturnOperationalSourceError,
        match="sales TradeReturnEvent",
    ):
        service._validate_operational_event(
            company_id=1,
            event=value,
            created_by=7,
        )


def test_non_receipt_target_is_rejected():
    value = event(
        return_document_type="issue"
    )

    with pytest.raises(
        service.SalesReturnOperationalSourceError,
        match="RECEIPT",
    ):
        service._validate_operational_event(
            company_id=1,
            event=value,
            created_by=7,
        )


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


class LoadDB:
    def __init__(
        self,
        value,
    ):
        self.value = value

    async def execute(
        self,
        statement,
    ):
        return ScalarResult(
            self.value
        )


@pytest.mark.asyncio
async def test_loader_returns_locked_event():
    value = event()

    result = (
        await service._load_sales_return_operational_event(
            LoadDB(
                value
            ),
            company_id=1,
            trade_return_event_id=100,
        )
    )

    assert result is value


@pytest.mark.asyncio
async def test_loader_missing_event_is_error():
    with pytest.raises(
        service.SalesReturnOperationalNotFoundError
    ):
        await service._load_sales_return_operational_event(
            LoadDB(
                None
            ),
            company_id=1,
            trade_return_event_id=100,
        )


@pytest.mark.asyncio
async def test_public_entrypoint_loads_then_applies(
    monkeypatch,
):
    value = event()

    expected = SimpleNamespace(
        trade_return_event=value
    )

    loader = AsyncMock(
        return_value=value
    )

    apply = AsyncMock(
        return_value=expected
    )

    monkeypatch.setattr(
        service,
        "_load_sales_return_operational_event",
        loader,
    )

    monkeypatch.setattr(
        service,
        "_apply_loaded_sales_return_operational_event",
        apply,
    )

    db = object()

    result = (
        await service.apply_sales_return_operational_event(
            db,
            company_id=1,
            trade_return_event_id=100,
            created_by=7,
        )
    )

    assert result is expected

    loader.assert_awaited_once_with(
        db,
        company_id=1,
        trade_return_event_id=100,
    )

    apply.assert_awaited_once_with(
        db,
        company_id=1,
        event=value,
        created_by=7,
    )
