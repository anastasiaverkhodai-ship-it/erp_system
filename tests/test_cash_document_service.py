import inspect
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.cash_document_service import (
    _normalize_currency,
    _normalize_number,
    _normalize_optional_text,
)
from app.services.cash_document_types import (
    CashDocumentFacts,
    CashDocumentValidationError,
)
from app.services.payment_types import PaymentDirection


def test_cash_document_fact_normalizers():
    assert _normalize_currency(" uah ") == "UAH"
    assert _normalize_number(" CD-1 ") == "CD-1"
    assert _normalize_optional_text(" note ") == "note"
    assert _normalize_optional_text("   ") is None
    assert _normalize_optional_text(None) is None


def test_invalid_currency_fails_closed():
    with pytest.raises(
        CashDocumentValidationError
    ):
        _normalize_currency("UA")


def test_blank_number_fails_closed():
    with pytest.raises(
        CashDocumentValidationError
    ):
        _normalize_number("   ")


def test_cash_document_facts_are_frozen():
    facts = CashDocumentFacts(
        document_number="CD-1",
        direction=PaymentDirection.INCOMING,
        document_date=__import__(
            "datetime"
        ).date.today(),
        amount=Decimal("100.00"),
        currency_code="UAH",
        counterparty_id=1,
    )

    with pytest.raises(
        __import__("dataclasses").FrozenInstanceError
    ):
        facts.document_number = "OTHER"


def test_service_owns_no_transaction():
    source = Path(
        "app/services/cash_document_service.py"
    ).read_text()

    assert ".commit(" not in source
    assert ".rollback(" not in source


def test_service_does_not_create_payment():
    source = Path(
        "app/services/cash_document_service.py"
    ).read_text()

    assert "Payment(" not in source
    assert "create_payment_draft(" not in source
    assert "confirm_payment(" not in source


def test_service_does_not_post_gl():
    source = Path(
        "app/services/cash_document_service.py"
    ).read_text()

    assert "JournalEntry(" not in source
    assert "post_journal_entry" not in source
    assert "generate_and_post_payment_journal_entry" not in source


def test_public_service_functions_are_async():
    from app.services import cash_document_service as service

    names = (
        "get_cash_document",
        "create_cash_document_evidence",
        "reverse_cash_document_evidence",
        "create_cash_document_reentry_evidence",
    )

    for name in names:
        assert inspect.iscoroutinefunction(
            getattr(service, name)
        )
