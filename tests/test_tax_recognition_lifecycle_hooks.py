import inspect

import app.services.invoice_fulfillment_allocation_service as fulfillment
import app.services.payment_settlement_service as settlement


def test_fulfillment_create_has_vat_hook():
    source = inspect.getsource(
        fulfillment
        .create_invoice_fulfillment_allocation
    )

    assert (
        "reconcile_output_tax_for_invoice_line"
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
        "reconcile_output_tax_for_invoice_line"
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
        "reconcile_output_tax_for_invoice"
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
        "reconcile_output_tax_for_invoice"
        in source
    )

    assert (
        "allocation.reversed_at.date()"
        in source
    )


def test_settlement_accounting_still_precedes_vat_hook():
    source = inspect.getsource(
        settlement
        .create_payment_settlement_allocation
    )

    accounting_position = source.index(
        "generate_and_post_settlement_journal_entry"
    )

    vat_position = source.index(
        "reconcile_output_tax_for_invoice"
    )

    assert (
        accounting_position
        < vat_position
    )
