from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.tax_recognition_accounting_service import (
    OutputVatRecognitionSourceKind,
)
from app.services.tax_recognition_journal_service import (
    TaxRecognitionJournalCurrencyError,
    TaxRecognitionJournalSourceStateError,
    generate_and_post_output_vat_recognition_journal_entry,
    resolve_output_vat_recognition_source_kind,
    reverse_output_vat_recognition_journal_entry,
    validate_output_vat_recognition_accounting_currency,
)


D1 = date(
    2026,
    9,
    1,
)


def event(
    *,
    event_id=10,
    company_id=1,
    fulfillment_id=20,
    settlement_id=None,
    amount=Decimal("20.00"),
    currency="UAH",
    reversal_of_id=None,
):
    return SimpleNamespace(
        id=event_id,
        company_id=company_id,
        invoice_fulfillment_allocation_id=(
            fulfillment_id
        ),
        payment_settlement_allocation_id=(
            settlement_id
        ),
        recognition_date=D1,
        recognized_taxable_base=(
            Decimal("100.00")
        ),
        recognized_tax_amount=amount,
        currency_code=currency,
        reversal_of_id=reversal_of_id,
    )


def test_resolves_fulfillment_source():
    result = (
        resolve_output_vat_recognition_source_kind(
            event()
        )
    )

    assert (
        result
        == OutputVatRecognitionSourceKind
        .FULFILLMENT
    )


def test_resolves_settlement_source():
    result = (
        resolve_output_vat_recognition_source_kind(
            event(
                fulfillment_id=None,
                settlement_id=30,
            )
        )
    )

    assert (
        result
        == OutputVatRecognitionSourceKind
        .SETTLEMENT
    )


@pytest.mark.parametrize(
    (
        "fulfillment_id",
        "settlement_id",
    ),
    (
        (
            None,
            None,
        ),
        (
            20,
            30,
        ),
    ),
)
def test_requires_exactly_one_typed_source(
    fulfillment_id,
    settlement_id,
):
    with pytest.raises(
        TaxRecognitionJournalSourceStateError
    ):
        resolve_output_vat_recognition_source_kind(
            event(
                fulfillment_id=fulfillment_id,
                settlement_id=settlement_id,
            )
        )


@pytest.mark.parametrize(
    (
        "fulfillment_id",
        "settlement_id",
    ),
    (
        (
            0,
            None,
        ),
        (
            None,
            0,
        ),
    ),
)
def test_requires_positive_source_id(
    fulfillment_id,
    settlement_id,
):
    with pytest.raises(
        TaxRecognitionJournalSourceStateError
    ):
        resolve_output_vat_recognition_source_kind(
            event(
                fulfillment_id=fulfillment_id,
                settlement_id=settlement_id,
            )
        )


def test_uah_currency_is_supported():
    validate_output_vat_recognition_accounting_currency(
        event()
    )


def test_foreign_currency_fails_closed():
    with pytest.raises(
        TaxRecognitionJournalCurrencyError
    ):
        validate_output_vat_recognition_accounting_currency(
            event(
                currency="EUR"
            )
        )


@pytest.mark.asyncio
async def test_zero_tax_amount_is_gl_noop():
    result = (
        await generate_and_post_output_vat_recognition_journal_entry(
            object(),
            event=event(
                amount=Decimal("0.00")
            ),
            created_by=1,
        )
    )

    assert result is None


@pytest.mark.asyncio
async def test_reversal_event_cannot_generate_original_gl():
    with pytest.raises(
        TaxRecognitionJournalSourceStateError
    ):
        await generate_and_post_output_vat_recognition_journal_entry(
            object(),
            event=event(
                reversal_of_id=5,
            ),
            created_by=1,
        )


@pytest.mark.asyncio
async def test_invalid_created_by_fails_closed():
    with pytest.raises(
        TaxRecognitionJournalSourceStateError
    ):
        await generate_and_post_output_vat_recognition_journal_entry(
            object(),
            event=event(),
            created_by=0,
        )

@pytest.mark.asyncio
async def test_original_event_cannot_be_used_as_reversal():
    with pytest.raises(
        TaxRecognitionJournalSourceStateError
    ):
        await reverse_output_vat_recognition_journal_entry(
            object(),
            reversal_event=event(),
            reversed_by=1,
        )


@pytest.mark.asyncio
async def test_zero_tax_reversal_is_gl_noop():
    result = (
        await reverse_output_vat_recognition_journal_entry(
            object(),
            reversal_event=event(
                amount=Decimal("0.00"),
                reversal_of_id=5,
            ),
            reversed_by=1,
        )
    )

    assert result is None


@pytest.mark.asyncio
async def test_tax_reversal_requires_valid_user():
    with pytest.raises(
        TaxRecognitionJournalSourceStateError
    ):
        await reverse_output_vat_recognition_journal_entry(
            object(),
            reversal_event=event(
                reversal_of_id=5,
            ),
            reversed_by=0,
        )
