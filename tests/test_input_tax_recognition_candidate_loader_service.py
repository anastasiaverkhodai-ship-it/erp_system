from types import SimpleNamespace

import pytest

from app.models.document import (
    DocumentType,
)
from app.services.counterparty_open_item_types import (
    CounterpartyOpenItemType,
)
from app.services.input_tax_recognition_candidate_loader_service import (
    InputTaxRecognitionCandidateLoaderStateError,
    candidate_kinds_for_method,
    validate_input_purchase_calculation,
)
from app.services.payment_types import (
    PaymentDirection,
)
from app.services.tax_recognition_types import (
    TaxRecognitionMethod,
)
from app.services.tax_types import (
    TaxDirection,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)


def calculation(
    *,
    direction=TaxDirection.INPUT,
    method=TaxRecognitionMethod.FIRST_EVENT,
):
    return SimpleNamespace(
        id=10,
        company_id=1,
        trade_document_id=20,
        trade_document_line_id=30,
        direction=direction,
        recognition_method=method,
        currency_code="UAH",
    )


def invoice(
    *,
    direction=TradeDirection.PURCHASE,
    kind=TradeDocumentKind.INVOICE,
    status=TradeDocumentStatus.CONFIRMED,
):
    return SimpleNamespace(
        id=20,
        company_id=1,
        direction=direction,
        kind=kind,
        status=status,
        currency_code="UAH",
    )


def test_purchase_first_event_context_is_valid():
    method = validate_input_purchase_calculation(
        calculation=calculation(),
        invoice=invoice(),
    )

    assert (
        method
        == TaxRecognitionMethod.FIRST_EVENT
    )


def test_first_event_loads_receipt_and_settlement_kinds():
    assert candidate_kinds_for_method(
        TaxRecognitionMethod.FIRST_EVENT
    ) == (
        "fulfillment",
        "settlement",
    )


def test_cash_method_loads_settlement_only():
    assert candidate_kinds_for_method(
        TaxRecognitionMethod.CASH_METHOD
    ) == (
        "settlement",
    )


def test_output_calculation_is_rejected():
    with pytest.raises(
        InputTaxRecognitionCandidateLoaderStateError
    ):
        validate_input_purchase_calculation(
            calculation=calculation(
                direction=TaxDirection.OUTPUT
            ),
            invoice=invoice(),
        )


def test_sale_invoice_is_rejected():
    with pytest.raises(
        InputTaxRecognitionCandidateLoaderStateError
    ):
        validate_input_purchase_calculation(
            calculation=calculation(),
            invoice=invoice(
                direction=TradeDirection.SALE
            ),
        )


def test_unconfirmed_purchase_invoice_is_rejected():
    with pytest.raises(
        InputTaxRecognitionCandidateLoaderStateError
    ):
        validate_input_purchase_calculation(
            calculation=calculation(),
            invoice=invoice(
                status=TradeDocumentStatus.DRAFT
            ),
        )


def test_manual_method_is_rejected():
    with pytest.raises(
        InputTaxRecognitionCandidateLoaderStateError
    ):
        validate_input_purchase_calculation(
            calculation=calculation(
                method=TaxRecognitionMethod.MANUAL
            ),
            invoice=invoice(),
        )


def test_existing_purchase_semantic_enums_are_stable():
    assert (
        DocumentType.RECEIPT.value
        == "receipt"
    )

    assert (
        CounterpartyOpenItemType.PAYABLE.value
        == "payable"
    )

    assert (
        PaymentDirection.OUTGOING.value
        == "outgoing"
    )

    assert (
        TradeDirection.PURCHASE.value
        == "purchase"
    )
