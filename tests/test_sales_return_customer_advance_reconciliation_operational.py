from datetime import date
from types import SimpleNamespace

import pytest

import app.services.sales_return_operational_service as service

from app.services.customer_advance_clearing_lifecycle_service import (
    CustomerAdvanceClearingLifecycleError,
)

from app.services.sales_return_operational_service import (
    SalesReturnOperationalError,
)


@pytest.mark.asyncio
async def test_customer_advance_runs_after_economic_before_cost(
    monkeypatch,
):
    order = []

    event = SimpleNamespace(
        original_fulfillment_id=10,
        original_fulfillment_line_id=20,
        return_date=date(
            2026,
            9,
            5,
        ),
    )

    monkeypatch.setattr(
        service,
        "_validate_operational_event",
        lambda **kwargs: None,
    )

    async def quantity(
        db,
        *,
        event,
    ):
        order.append(
            "quantity"
        )

        return "quantity-result"

    economic_result = SimpleNamespace(
        created_events=(
            SimpleNamespace(
                sales_recognition_event_id=31,
            ),
        )
    )

    async def economic(
        db,
        **kwargs,
    ):
        order.append(
            "economic"
        )

        return economic_result

    async def impacted(
        db,
        *,
        company_id,
        economic_result,
    ):
        order.append(
            "resolve-cac"
        )

        assert company_id == 1

        return (
            901,
            902,
        )

    async def customer_advance(
        db,
        *,
        company_id,
        invoice_id,
        adjustment_date,
        created_by,
    ):
        order.append(
            f"cac-{invoice_id}"
        )

        assert company_id == 1
        assert adjustment_date == event.return_date
        assert created_by == 7

        return SimpleNamespace(
            created_events=()
        )

    async def cost(
        db,
        **kwargs,
    ):
        order.append(
            "cost"
        )

        return "cost-result"

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
        "_load_impacted_customer_advance_invoice_ids",
        impacted,
    )

    monkeypatch.setattr(
        service,
        "reconcile_customer_advance_clearing_lifecycle_for_invoice",
        customer_advance,
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
            event=event,
            created_by=7,
        )
    )

    assert order == [
        "quantity",
        "economic",
        "resolve-cac",
        "cac-901",
        "cac-902",
        "cost",
    ]

    assert (
        result.economic_result
        is economic_result
    )

    assert (
        result.cost_result
        == "cost-result"
    )


@pytest.mark.asyncio
async def test_customer_advance_failure_blocks_cost(
    monkeypatch,
):
    order = []

    event = SimpleNamespace(
        original_fulfillment_id=10,
        original_fulfillment_line_id=20,
        return_date=date(
            2026,
            9,
            5,
        ),
    )

    monkeypatch.setattr(
        service,
        "_validate_operational_event",
        lambda **kwargs: None,
    )

    async def quantity(
        db,
        *,
        event,
    ):
        order.append(
            "quantity"
        )

        return object()

    async def economic(
        db,
        **kwargs,
    ):
        order.append(
            "economic"
        )

        return SimpleNamespace(
            created_events=(
                SimpleNamespace(
                    sales_recognition_event_id=31,
                ),
            )
        )

    async def impacted(
        db,
        **kwargs,
    ):
        order.append(
            "resolve-cac"
        )

        return (
            901,
        )

    async def customer_advance(
        db,
        **kwargs,
    ):
        order.append(
            "cac"
        )

        raise CustomerAdvanceClearingLifecycleError(
            "fixture failure"
        )

    async def cost(
        db,
        **kwargs,
    ):
        order.append(
            "cost"
        )

        return object()

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
        "_load_impacted_customer_advance_invoice_ids",
        impacted,
    )

    monkeypatch.setattr(
        service,
        "reconcile_customer_advance_clearing_lifecycle_for_invoice",
        customer_advance,
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_return_cost_restoration_lifecycle_for_fulfillment_line",
        cost,
    )

    with pytest.raises(
        SalesReturnOperationalError,
        match=(
            "customer advance reconciliation failed"
        ),
    ):
        await service._apply_loaded_sales_return_operational_event(
            object(),
            company_id=1,
            event=event,
            created_by=7,
        )

    assert order == [
        "quantity",
        "economic",
        "resolve-cac",
        "cac",
    ]


@pytest.mark.asyncio
async def test_no_economic_changes_skip_customer_advance_queries():
    class NoDatabaseAccess:
        async def execute(
            self,
            statement,
        ):
            raise AssertionError(
                "DB must not be queried"
            )

    result = (
        await service._load_impacted_customer_advance_invoice_ids(
            NoDatabaseAccess(),
            company_id=1,
            economic_result=SimpleNamespace(
                created_events=()
            ),
        )
    )

    assert result == ()


@pytest.mark.asyncio
async def test_missing_created_events_attribute_is_noop():
    class NoDatabaseAccess:
        async def execute(
            self,
            statement,
        ):
            raise AssertionError(
                "DB must not be queried"
            )

    result = (
        await service._load_impacted_customer_advance_invoice_ids(
            NoDatabaseAccess(),
            company_id=1,
            economic_result=SimpleNamespace(),
        )
    )

    assert result == ()
