from pathlib import Path


def function_source(
    path: str,
    function_name: str,
) -> str:
    text = Path(path).read_text()

    start = text.index(
        f"async def {function_name}("
    )

    next_async = text.find(
        "\nasync def ",
        start + 1,
    )

    next_class = text.find(
        "\nclass ",
        start + 1,
    )

    candidates = [
        value
        for value in (
            next_async,
            next_class,
        )
        if value != -1
    ]

    end = (
        min(candidates)
        if candidates
        else len(text)
    )

    return text[
        start:end
    ]


def test_invoice_cancel_guard_precedes_open_item_cancel():
    source = function_source(
        "app/services/"
        "trade_document_lifecycle_service.py",
        "_cancel_trade_invoice",
    )

    guard = source.index(
        "has_active_invoice_allocations("
    )

    open_item_cancel = source.index(
        "cancel_counterparty_open_item_for_invoice("
    )

    assert guard < open_item_cancel


def test_sales_reversal_guard_precedes_warehouse_reversal():
    source = function_source(
        "app/services/"
        "trade_fulfillment_service.py",
        "execute_sales_order_fulfillment_reversal",
    )

    guard = source.index(
        "has_active_fulfillment_allocations("
    )

    warehouse_reversal = source.index(
        "reverse_document_for_trade_fulfillment("
    )

    assert guard < warehouse_reversal


def test_purchase_reversal_guard_precedes_warehouse_reversal():
    source = function_source(
        "app/services/"
        "trade_fulfillment_service.py",
        "execute_purchase_order_fulfillment_reversal",
    )

    guard = source.index(
        "has_active_fulfillment_allocations("
    )

    warehouse_reversal = source.index(
        "reverse_document_for_trade_fulfillment("
    )

    assert guard < warehouse_reversal
