import inspect

from app.services.invoice_fulfillment_allocation_service import (
    create_invoice_fulfillment_allocation,
    reverse_invoice_fulfillment_allocation,
)


SALES_CALL = (
    "reconcile_sales_recognition_"
    "lifecycle_for_invoice_line"
)

VAT_CALL = (
    "reconcile_tax_for_invoice_line"
)


def _assert_hook_order(
    function,
):
    source = inspect.getsource(
        function
    )

    flush_position = source.index(
        "await db.flush()"
    )

    sales_position = source.index(
        SALES_CALL
    )

    vat_position = source.index(
        VAT_CALL
    )

    assert (
        flush_position
        < sales_position
        < vat_position
    )


def test_create_allocation_runs_sales_before_vat():
    _assert_hook_order(
        create_invoice_fulfillment_allocation
    )


def test_reverse_allocation_runs_sales_before_vat():
    _assert_hook_order(
        reverse_invoice_fulfillment_allocation
    )
