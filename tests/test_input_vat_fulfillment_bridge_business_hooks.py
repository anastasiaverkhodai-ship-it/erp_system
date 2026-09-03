import inspect
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.input_vat_fulfillment_bridge_lifecycle_service as lifecycle
from app.services.invoice_fulfillment_allocation_service import (
    create_invoice_fulfillment_allocation,
    reverse_invoice_fulfillment_allocation,
)


BRIDGE_CALL = (
    "reconcile_input_vat_fulfillment_bridge_"
    "lifecycle_for_invoice_line"
)

SALES_CALL = (
    "reconcile_sales_recognition_"
    "lifecycle_for_invoice_line"
)

TAX_CALL = (
    "reconcile_tax_for_invoice_line"
)

D1 = date(
    2026,
    8,
    30,
)


class ScalarCollection:
    def __init__(
        self,
        values,
    ):
        self.values = tuple(
            values
        )

    def scalars(
        self,
    ):
        return self

    def all(
        self,
    ):
        return list(
            self.values
        )


def test_create_business_hook_order():
    source = inspect.getsource(
        create_invoice_fulfillment_allocation
    )

    flush_position = source.index(
        "await db.flush()"
    )

    sales_position = source.index(
        SALES_CALL
    )

    bridge_position = source.index(
        BRIDGE_CALL
    )

    tax_position = source.index(
        TAX_CALL
    )

    assert (
        flush_position
        < sales_position
        < bridge_position
        < tax_position
    )

    assert (
        "InputVatFulfillmentBridgeLifecycleError"
        in source
    )


def test_reverse_business_hook_order():
    source = inspect.getsource(
        reverse_invoice_fulfillment_allocation
    )

    flush_position = source.index(
        "await db.flush()"
    )

    sales_position = source.index(
        SALES_CALL
    )

    tax_position = source.index(
        TAX_CALL
    )

    bridge_position = source.index(
        BRIDGE_CALL
    )

    assert (
        flush_position
        < sales_position
        < tax_position
        < bridge_position
    )

    assert (
        "allocation.reversed_at.date()"
        in source
    )

    assert (
        "InputVatFulfillmentBridgeLifecycleError"
        in source
    )


@pytest.mark.asyncio
async def test_line_lifecycle_dispatches_only_selected_calculations(
    monkeypatch,
):
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=(
                ScalarCollection(
                    (
                        31,
                        32,
                    )
                )
            )
        )
    )

    calls = []

    async def reconcile(
        db,
        *,
        company_id,
        tax_calculation_id,
        adjustment_date,
        created_by,
    ):
        calls.append(
            (
                company_id,
                tax_calculation_id,
                adjustment_date,
                created_by,
            )
        )

        return SimpleNamespace(
            tax_calculation_id=(
                tax_calculation_id
            )
        )

    monkeypatch.setattr(
        lifecycle,
        (
            "reconcile_input_vat_fulfillment_bridge_"
            "lifecycle_for_tax_calculation"
        ),
        reconcile,
    )

    result = (
        await lifecycle
        .reconcile_input_vat_fulfillment_bridge_lifecycle_for_invoice_line(
            db,
            company_id=1,
            invoice_id=10,
            invoice_line_id=20,
            adjustment_date=D1,
            created_by=7,
        )
    )

    assert calls == [
        (
            1,
            31,
            D1,
            7,
        ),
        (
            1,
            32,
            D1,
            7,
        ),
    ]

    assert tuple(
        item.tax_calculation_id
        for item in result
    ) == (
        31,
        32,
    )


@pytest.mark.asyncio
async def test_line_lifecycle_is_noop_without_input_vat_calculation(
    monkeypatch,
):
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=(
                ScalarCollection(
                    ()
                )
            )
        )
    )

    reconcile = AsyncMock()

    monkeypatch.setattr(
        lifecycle,
        (
            "reconcile_input_vat_fulfillment_bridge_"
            "lifecycle_for_tax_calculation"
        ),
        reconcile,
    )

    result = (
        await lifecycle
        .reconcile_input_vat_fulfillment_bridge_lifecycle_for_invoice_line(
            db,
            company_id=1,
            invoice_id=10,
            invoice_line_id=20,
            adjustment_date=D1,
            created_by=7,
        )
    )

    assert result == ()

    reconcile.assert_not_awaited()


@pytest.mark.parametrize(
    (
        "company_id",
        "invoice_id",
        "invoice_line_id",
        "created_by",
    ),
    [
        (
            0,
            1,
            1,
            1,
        ),
        (
            1,
            0,
            1,
            1,
        ),
        (
            1,
            1,
            0,
            1,
        ),
        (
            1,
            1,
            1,
            0,
        ),
    ],
)
@pytest.mark.asyncio
async def test_line_lifecycle_validates_context(
    company_id,
    invoice_id,
    invoice_line_id,
    created_by,
):
    with pytest.raises(
        ValueError,
    ):
        await lifecycle.reconcile_input_vat_fulfillment_bridge_lifecycle_for_invoice_line(
            object(),
            company_id=company_id,
            invoice_id=invoice_id,
            invoice_line_id=(
                invoice_line_id
            ),
            adjustment_date=D1,
            created_by=created_by,
        )
