from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.services.vat_advance_bridge_journal_service as service

from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.models.vat_advance_bridge_event import (
    VatAdvanceBridgeEvent,
)
from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.vat_advance_bridge_journal_service import (
    VatAdvanceBridgeJournalCurrencyError,
    VatAdvanceBridgeJournalDuplicateError,
    VatAdvanceBridgeJournalNotFoundError,
    VatAdvanceBridgeJournalSourceStateError,
    generate_and_post_vat_advance_bridge_journal_entry,
    get_original_vat_advance_bridge_journal_entry,
    reverse_vat_advance_bridge_journal_entry,
)


D1 = date(2026, 8, 31)
D2 = date(2026, 9, 1)


def event(
    *,
    event_id=10,
    company_id=1,
    amount=Decimal("20.00"),
    currency_code="UAH",
    reversal_of_id=None,
    bridge_date=D1,
):
    return VatAdvanceBridgeEvent(
        id=event_id,
        company_id=company_id,
        tax_calculation_id=100,
        invoice_fulfillment_allocation_id=200,
        bridge_date=bridge_date,
        bridged_tax_amount=amount,
        currency_code=currency_code,
        created_by=1,
        reversal_of_id=reversal_of_id,
    )


class Result:
    def __init__(
        self,
        value=None,
    ):
        self.value = value

    def scalar_one_or_none(
        self,
    ):
        return self.value


class FakeDb:
    def __init__(
        self,
        execute_values=None,
    ):
        self.execute_values = list(
            execute_values or []
        )
        self.added = []
        self.flush_count = 0

    async def execute(
        self,
        statement,
    ):
        if not self.execute_values:
            return Result(
                None
            )

        return Result(
            self.execute_values.pop(
                0
            )
        )

    def add(
        self,
        value,
    ):
        self.added.append(
            value
        )

    async def flush(
        self,
    ):
        self.flush_count += 1


def test_currency_requires_uah():
    with pytest.raises(
        VatAdvanceBridgeJournalCurrencyError,
        match="supports UAH only",
    ):
        service.validate_vat_advance_bridge_accounting_currency(
            event(
                currency_code="EUR",
            )
        )


@pytest.mark.parametrize(
    "event_id",
    [
        None,
        0,
        -1,
    ],
)
def test_event_requires_positive_persistent_id(
    event_id,
):
    with pytest.raises(
        VatAdvanceBridgeJournalSourceStateError,
        match="persistent positive ID",
    ):
        service._validate_event_identity(
            event(
                event_id=event_id,
            )
        )


def test_event_requires_positive_company_id():
    with pytest.raises(
        VatAdvanceBridgeJournalSourceStateError,
        match="company_id",
    ):
        service._validate_event_identity(
            event(
                company_id=0,
            )
        )


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("0"),
        Decimal("-0.01"),
    ],
)
def test_event_amount_requires_positive_amount(
    amount,
):
    with pytest.raises(
        VatAdvanceBridgeJournalSourceStateError,
        match="must be greater than zero",
    ):
        service._event_amount(
            event(
                amount=amount,
            )
        )


def test_original_generation_rejects_reversal_event():
    db = FakeDb()

    with pytest.raises(
        VatAdvanceBridgeJournalSourceStateError,
        match="cannot generate an original",
    ):
        import asyncio

        asyncio.run(
            generate_and_post_vat_advance_bridge_journal_entry(
                db,
                event=event(
                    reversal_of_id=9,
                ),
                created_by=1,
            )
        )


def test_original_generation_rejects_duplicate():
    db = FakeDb(
        execute_values=[
            999,
        ]
    )

    with pytest.raises(
        VatAdvanceBridgeJournalDuplicateError,
        match="already exists",
    ):
        import asyncio

        asyncio.run(
            generate_and_post_vat_advance_bridge_journal_entry(
                db,
                event=event(),
                created_by=1,
            )
        )


@pytest.mark.asyncio
async def test_original_generation_builds_bridge_typed_journal(
    monkeypatch,
):
    db = FakeDb(
        execute_values=[
            None,
        ]
    )

    async def fake_resolve(
        db,
        *,
        company_id,
        roles,
    ):
        assert company_id == 1

        assert roles == (
            AccountingAccountRole
            .GOODS_REVENUE,
            AccountingAccountRole
            .VAT_OUTPUT,
        )

        return {
            AccountingAccountRole.GOODS_REVENUE:
                SimpleNamespace(
                    id=702,
                ),
            AccountingAccountRole.VAT_OUTPUT:
                SimpleNamespace(
                    id=643,
                ),
        }

    async def fake_validate(
        *,
        db,
        journal_entry,
    ):
        assert (
            journal_entry.status
            == JournalEntryStatus.DRAFT
        )

    async def fake_post(
        *,
        db,
        company_id,
        journal_entry_id,
    ):
        assert company_id == 1
        assert journal_entry_id == 555

        journal = db.added[-1]

        journal.status = (
            JournalEntryStatus.POSTED
        )

        return journal

    original_flush = db.flush

    async def flush_with_id():
        await original_flush()

        if db.added:
            journal = db.added[-1]

            if journal.id is None:
                journal.id = 555

    monkeypatch.setattr(
        service,
        "resolve_company_account_roles",
        fake_resolve,
    )

    monkeypatch.setattr(
        service,
        "validate_journal_entry",
        fake_validate,
    )

    monkeypatch.setattr(
        service,
        "post_journal_entry",
        fake_post,
    )

    db.flush = flush_with_id

    result = (
        await generate_and_post_vat_advance_bridge_journal_entry(
            db,
            event=event(),
            created_by=1,
        )
    )

    assert (
        result.vat_advance_bridge_event_id
        == 10
    )

    assert result.document_id is None
    assert result.payment_id is None

    assert (
        result.payment_settlement_allocation_id
        is None
    )

    assert (
        result.tax_recognition_event_id
        is None
    )

    assert (
        result.sales_recognition_event_id
        is None
    )

    assert result.entry_date == D1

    assert len(
        result.lines
    ) == 2

    assert (
        result.lines[0].account_id
        == 702
    )

    assert (
        result.lines[0].debit
        == Decimal("20.00")
    )

    assert (
        result.lines[0].credit
        == Decimal("0")
    )

    assert (
        result.lines[1].account_id
        == 643
    )

    assert (
        result.lines[1].debit
        == Decimal("0")
    )

    assert (
        result.lines[1].credit
        == Decimal("20.00")
    )


@pytest.mark.asyncio
async def test_get_original_returns_matching_entry():
    journal = SimpleNamespace(
        id=77,
    )

    db = FakeDb(
        execute_values=[
            journal,
        ]
    )

    result = (
        await get_original_vat_advance_bridge_journal_entry(
            db,
            company_id=1,
            vat_advance_bridge_event_id=10,
        )
    )

    assert result is journal


@pytest.mark.asyncio
async def test_get_original_raises_when_missing():
    db = FakeDb(
        execute_values=[
            None,
        ]
    )

    with pytest.raises(
        VatAdvanceBridgeJournalNotFoundError,
        match="not found",
    ):
        await get_original_vat_advance_bridge_journal_entry(
            db,
            company_id=1,
            vat_advance_bridge_event_id=10,
        )


@pytest.mark.asyncio
async def test_reversal_calls_generic_reversal_with_bridge_override(
    monkeypatch,
):
    reversal = event(
        event_id=11,
        reversal_of_id=10,
        bridge_date=D2,
    )

    original = SimpleNamespace(
        id=70,
    )

    expected = SimpleNamespace(
        id=71,
    )

    async def fake_get_original(
        db,
        *,
        company_id,
        vat_advance_bridge_event_id,
        lock,
    ):
        assert company_id == 1
        assert (
            vat_advance_bridge_event_id
            == 10
        )
        assert lock is True

        return original

    async def fake_reverse(
        *,
        db,
        company_id,
        journal_entry_id,
        reversal_date,
        reversed_by,
        vat_advance_bridge_event_id_override,
    ):
        assert company_id == 1
        assert journal_entry_id == 70
        assert reversal_date == D2
        assert reversed_by == 1

        assert (
            vat_advance_bridge_event_id_override
            == 11
        )

        return expected

    db = FakeDb(
        execute_values=[
            None,
        ]
    )

    monkeypatch.setattr(
        service,
        "get_original_vat_advance_bridge_journal_entry",
        fake_get_original,
    )

    monkeypatch.setattr(
        service,
        "reverse_journal_entry",
        fake_reverse,
    )

    result = (
        await reverse_vat_advance_bridge_journal_entry(
            db,
            reversal_event=reversal,
            reversed_by=1,
        )
    )

    assert result is expected


@pytest.mark.asyncio
async def test_reversal_rejects_non_reversal_event():
    db = FakeDb()

    with pytest.raises(
        VatAdvanceBridgeJournalSourceStateError,
        match="Only a VAT Advance Bridge reversal",
    ):
        await reverse_vat_advance_bridge_journal_entry(
            db,
            reversal_event=event(),
            reversed_by=1,
        )


@pytest.mark.asyncio
async def test_reversal_rejects_duplicate_journal():
    db = FakeDb(
        execute_values=[
            999,
        ]
    )

    with pytest.raises(
        VatAdvanceBridgeJournalDuplicateError,
        match="already exists",
    ):
        await reverse_vat_advance_bridge_journal_entry(
            db,
            reversal_event=event(
                event_id=11,
                reversal_of_id=10,
                bridge_date=D2,
            ),
            reversed_by=1,
        )
