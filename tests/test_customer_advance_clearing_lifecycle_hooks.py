import inspect

import app.services.invoice_fulfillment_allocation_service as fulfillment_service
import app.services.payment_settlement_service as payment_service


CUSTOMER_CALL = (
    "reconcile_customer_advance_"
    "clearing_lifecycle_for_invoice("
)

SALES_CALL = (
    "reconcile_sales_recognition_"
    "lifecycle_for_invoice_line("
)


def test_payment_create_has_no_legacy_settlement_gl():
    source = inspect.getsource(
        payment_service
        .create_payment_settlement_allocation
    )

    assert (
        "generate_and_post_settlement_journal_entry("
        not in source
    )

    assert CUSTOMER_CALL in source

    assert (
        "CounterpartyOpenItemType.RECEIVABLE"
        in source
    )


def test_payment_reverse_has_no_legacy_settlement_gl():
    source = inspect.getsource(
        payment_service
        .reverse_payment_settlement_allocation
    )

    assert (
        "reverse_settlement_journal_entry("
        not in source
    )

    assert CUSTOMER_CALL in source

    assert (
        source.index(
            "PaymentSettlementAllocationStatus.REVERSED"
        )
        < source.index(
            CUSTOMER_CALL
        )
    )


def test_payment_tax_reconciles_before_customer_clearing():
    create_source = inspect.getsource(
        payment_service
        .create_payment_settlement_allocation
    )

    reverse_source = inspect.getsource(
        payment_service
        .reverse_payment_settlement_allocation
    )

    assert (
        create_source.index(
            "reconcile_tax_for_invoice("
        )
        < create_source.index(
            CUSTOMER_CALL
        )
    )

    assert (
        reverse_source.index(
            "reconcile_tax_for_invoice("
        )
        < reverse_source.index(
            CUSTOMER_CALL
        )
    )


def test_fulfillment_create_runs_sales_before_customer():
    source = inspect.getsource(
        fulfillment_service
        .create_invoice_fulfillment_allocation
    )

    assert (
        source.index(
            SALES_CALL
        )
        < source.index(
            CUSTOMER_CALL
        )
    )

    assert (
        source.index(
            CUSTOMER_CALL
        )
        < source.index(
            "reconcile_tax_for_invoice_line("
        )
    )

    assert (
        "TradeDirection.SALE"
        in source
    )


def test_fulfillment_reverse_runs_sales_before_customer():
    source = inspect.getsource(
        fulfillment_service
        .reverse_invoice_fulfillment_allocation
    )

    assert (
        source.index(
            SALES_CALL
        )
        < source.index(
            CUSTOMER_CALL
        )
    )

    assert (
        source.index(
            CUSTOMER_CALL
        )
        < source.index(
            "reconcile_tax_for_invoice_line("
        )
    )

    assert (
        "TradeDirection.SALE"
        in source
    )


def test_purchase_supplier_lifecycle_remains_present():
    create_source = inspect.getsource(
        fulfillment_service
        .create_invoice_fulfillment_allocation
    )

    reverse_source = inspect.getsource(
        fulfillment_service
        .reverse_invoice_fulfillment_allocation
    )

    supplier_call = (
        "reconcile_supplier_advance_"
        "clearing_lifecycle_for_invoice("
    )

    assert supplier_call in create_source
    assert supplier_call in reverse_source

    assert (
        "TradeDirection.PURCHASE"
        in create_source
    )

    assert (
        "TradeDirection.PURCHASE"
        in reverse_source
    )
