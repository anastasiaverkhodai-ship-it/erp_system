import inspect

import app.services.invoice_fulfillment_allocation_service as fulfillment
import app.services.payment_settlement_service as settlement


def test_fulfillment_create_has_vat_hook():
    source = inspect.getsource(
        fulfillment
        .create_invoice_fulfillment_allocation
    )

    assert (
        "reconcile_tax_for_invoice_line"
        in source
    )

    assert (
        "TaxRecognitionLifecycleError"
        in source
    )


def test_fulfillment_reverse_has_vat_hook():
    source = inspect.getsource(
        fulfillment
        .reverse_invoice_fulfillment_allocation
    )

    assert (
        "reconcile_tax_for_invoice_line"
        in source
    )

    assert (
        "allocation.reversed_at.date()"
        in source
    )


def test_settlement_create_has_vat_hook():
    source = inspect.getsource(
        settlement
        .create_payment_settlement_allocation
    )

    assert (
        "reconcile_tax_for_invoice"
        in source
    )

    assert (
        "open_item.trade_document_id"
        in source
    )


def test_settlement_reverse_has_vat_hook():
    source = inspect.getsource(
        settlement
        .reverse_payment_settlement_allocation
    )

    assert (
        "reconcile_tax_for_invoice"
        in source
    )

    assert (
        "allocation.reversed_at.date()"
        in source
    )


def test_settlement_tax_precedes_domain_clearing_lifecycles():
    import inspect
    import app.services.payment_settlement_service as payment_service

    create_source = inspect.getsource(
        payment_service
        .create_payment_settlement_allocation
    )

    reverse_source = inspect.getsource(
        payment_service
        .reverse_payment_settlement_allocation
    )

    customer_call = (
        "reconcile_customer_advance_"
        "clearing_lifecycle_for_invoice("
    )

    supplier_call = (
        "reconcile_supplier_advance_"
        "clearing_lifecycle_for_invoice("
    )

    assert (
        "generate_and_post_settlement_journal_entry("
        not in create_source
    )

    assert (
        "reverse_settlement_journal_entry("
        not in reverse_source
    )

    for source in (
        create_source,
        reverse_source,
    ):
        tax_index = source.index(
            "reconcile_tax_for_invoice("
        )

        assert (
            tax_index
            < source.index(
                customer_call
            )
        )

        assert (
            tax_index
            < source.index(
                supplier_call
            )
        )
