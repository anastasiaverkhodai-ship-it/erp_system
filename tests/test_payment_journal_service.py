from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.payment_journal_service import (
    PaymentJournalCurrencyError,
    PaymentJournalSourceStateError,
    validate_payment_accounting_currency,
)
from app.services.payment_types import (
    PaymentStatus,
)


def payment_stub(
    *,
    currency_code="UAH",
):
    return SimpleNamespace(
        currency_code=currency_code,
        status=PaymentStatus.CONFIRMED,
        amount=Decimal("100.00"),
    )


def test_uah_payment_accounting_currency_is_allowed():
    payment = payment_stub(
        currency_code="UAH"
    )

    validate_payment_accounting_currency(
        payment
    )


def test_non_uah_payment_accounting_fails_closed():
    payment = payment_stub(
        currency_code="EUR"
    )

    with pytest.raises(
        PaymentJournalCurrencyError
    ):
        validate_payment_accounting_currency(
            payment
        )


def test_payment_journal_source_error_is_domain_error():
    error = PaymentJournalSourceStateError(
        "bad state"
    )

    assert str(error) == "bad state"
