from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest

import app.services.input_vat_fulfillment_bridge_journal_service as service
from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.services.input_vat_fulfillment_bridge_journal_service import (
    InputVatFulfillmentBridgeJournalCurrencyError,
    InputVatFulfillmentBridgeJournalDuplicateError,
    InputVatFulfillmentBridgeJournalNotFoundError,
    InputVatFulfillmentBridgeJournalSourceStateError,
    generate_and_post_input_vat_fulfillment_bridge_journal_entry,
    get_original_input_vat_fulfillment_bridge_journal_entry,
    reverse_input_vat_fulfillment_bridge_journal_entry,
    validate_input_vat_fulfillment_bridge_accounting_currency,
)


D1 = date(
    2026,
    9,
    1,
)

D2 = date(
    2026,
    9,
    2,
)


class ScalarResult:
    def __init__(
        self,
        value,
    ):
        self.value = value

    def scalar_one_or_none(
        self,
    ):
        return self.value


def event(
    *,
    event_id=10,
    company_id=1,
    amount="20.00",
    currency="UAH",
    bridge_date=D1,
    reversal_of_id=None,
):
    return SimpleNamespace(
        id=event_id,
        company_id=company_id,
        bridged_tax_amount=Decimal(
            amount
        ),
        currency_code=currency,
        bridge_date=bridge_date,
        reversal_of_id=(
            reversal_of_id
        ),
    )


def test_currency_is_uah_only():
    validate_input_vat_fulfillment_bridge_accounting_currency(
        event()
    )

    with pytest.raises(
        InputVatFulfillmentBridgeJournalCurrencyError,
        match="UAH only",
    ):
        validate_input_vat_fulfillment_bridge_accounting_currency(
            event(
                currency="EUR"
            )
        )


@pytest.mark.parametrize(
    "amount",
    [
        "0",
        "-1",
        "NaN",
        "Infinity",
    ],
)
@pytest.mark.asyncio
async def test_original_requires_positive_finite_amount(
    amount,
):
    db = SimpleNamespace()

    with pytest.raises(
        InputVatFulfillmentBridgeJournalSourceStateError,
    ):
        await generate_and_post_input_vat_fulfillment_bridge_journal_entry(
            db,
            event=event(
                amount=amount
            ),
            created_by=1,
        )


@pytest.mark.asyncio
async def test_reversal_event_cannot_generate_original():
    db = SimpleNamespace()

    with pytest.raises(
        InputVatFulfillmentBridgeJournalSourceStateError,
        match="reversal event",
    ):
        await generate_and_post_input_vat_fulfillment_bridge_journal_entry(
            db,
            event=event(
                reversal_of_id=9
            ),
            created_by=1,
        )


@pytest.mark.asyncio
async def test_duplicate_original_is_rejected():
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=(
                ScalarResult(
                    501
                )
            )
        )
    )

    with pytest.raises(
        InputVatFulfillmentBridgeJournalDuplicateError,
        match="already exists",
    ):
        await generate_and_post_input_vat_fulfillment_bridge_journal_entry(
            db,
            event=event(),
            created_by=1,
        )


@pytest.mark.asyncio
async def test_original_constructs_typed_journal_entry(
    monkeypatch,
):
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=(
                ScalarResult(
                    None
                )
            )
        )
    )

    fake_plan = (
        SimpleNamespace(
            lines=()
        )
    )

    monkeypatch.setattr(
        service,
        (
            "create_input_vat_"
            "fulfillment_bridge_accounting_plan"
        ),
        Mock(
            return_value=fake_plan
        ),
    )

    monkeypatch.setattr(
        service,
        "_build_journal_lines",
        AsyncMock(
            return_value=[]
        ),
    )

    seen = {}

    async def validate_and_post(
        db,
        *,
        journal_entry,
    ):
        seen[
            "journal_entry"
        ] = journal_entry

        return journal_entry

    monkeypatch.setattr(
        service,
        "_validate_and_post",
        validate_and_post,
    )

    result = (
        await generate_and_post_input_vat_fulfillment_bridge_journal_entry(
            db,
            event=event(),
            created_by=7,
        )
    )

    entry = seen[
        "journal_entry"
    ]

    assert result is entry

    assert isinstance(
        entry,
        JournalEntry,
    )

    assert (
        entry.company_id
        == 1
    )

    assert (
        entry.input_vat_fulfillment_bridge_event_id
        == 10
    )

    assert entry.document_id is None
    assert entry.payment_id is None

    assert (
        entry.payment_settlement_allocation_id
        is None
    )

    assert (
        entry.tax_recognition_event_id
        is None
    )

    assert (
        entry.sales_recognition_event_id
        is None
    )

    assert (
        entry.vat_advance_bridge_event_id
        is None
    )

    assert (
        entry.accounting_rule_id
        is None
    )

    assert (
        entry.entry_date
        == D1
    )

    assert (
        entry.status
        == JournalEntryStatus.DRAFT
    )

    assert (
        entry.created_by
        == 7
    )

    service.create_input_vat_fulfillment_bridge_accounting_plan.assert_called_once_with(
        amount=Decimal(
            "20.00"
        )
    )


@pytest.mark.asyncio
async def test_original_lookup_not_found():
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=(
                ScalarResult(
                    None
                )
            )
        )
    )

    with pytest.raises(
        InputVatFulfillmentBridgeJournalNotFoundError,
        match="not found",
    ):
        await get_original_input_vat_fulfillment_bridge_journal_entry(
            db,
            company_id=1,
            input_vat_fulfillment_bridge_event_id=10,
        )


@pytest.mark.asyncio
async def test_reversal_requires_reversal_event():
    db = SimpleNamespace()

    with pytest.raises(
        InputVatFulfillmentBridgeJournalSourceStateError,
        match="Only an INPUT VAT",
    ):
        await reverse_input_vat_fulfillment_bridge_journal_entry(
            db,
            reversal_event=event(),
            reversed_by=1,
        )


@pytest.mark.asyncio
async def test_duplicate_reversal_is_rejected():
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=(
                ScalarResult(
                    700
                )
            )
        )
    )

    with pytest.raises(
        InputVatFulfillmentBridgeJournalDuplicateError,
        match="already exists",
    ):
        await reverse_input_vat_fulfillment_bridge_journal_entry(
            db,
            reversal_event=event(
                event_id=11,
                bridge_date=D2,
                reversal_of_id=10,
            ),
            reversed_by=1,
        )


@pytest.mark.asyncio
async def test_reversal_uses_typed_source_override(
    monkeypatch,
):
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=(
                ScalarResult(
                    None
                )
            )
        )
    )

    original = SimpleNamespace(
        id=501
    )

    lookup = AsyncMock(
        return_value=original
    )

    monkeypatch.setattr(
        service,
        (
            "get_original_input_vat_"
            "fulfillment_bridge_journal_entry"
        ),
        lookup,
    )

    seen = {}

    async def reverse(
        **kwargs,
    ):
        seen.update(
            kwargs
        )

        return SimpleNamespace(
            id=502
        )

    monkeypatch.setattr(
        service,
        "reverse_journal_entry",
        reverse,
    )

    reversal_event = event(
        event_id=11,
        bridge_date=D2,
        reversal_of_id=10,
    )

    result = (
        await reverse_input_vat_fulfillment_bridge_journal_entry(
            db,
            reversal_event=(
                reversal_event
            ),
            reversed_by=7,
        )
    )

    assert result.id == 502

    lookup.assert_awaited_once_with(
        db,
        company_id=1,
        input_vat_fulfillment_bridge_event_id=10,
        lock=True,
    )

    assert seen[
        "db"
    ] is db

    assert seen[
        "company_id"
    ] == 1

    assert seen[
        "journal_entry_id"
    ] == 501

    assert seen[
        "reversal_date"
    ] == D2

    assert seen[
        "reversed_by"
    ] == 7

    assert (
        seen[
            "input_vat_fulfillment_bridge_event_id_override"
        ]
        == 11
    )
