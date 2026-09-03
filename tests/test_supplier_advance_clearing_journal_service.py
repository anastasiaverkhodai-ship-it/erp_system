from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.supplier_advance_clearing_journal_service as service

from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.services.accounting_account_role_resolver import (
    AccountingRoleNotConfiguredError,
)
from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.supplier_advance_clearing_journal_service import (
    SupplierAdvanceClearingJournalCurrencyError,
    SupplierAdvanceClearingJournalDuplicateError,
    SupplierAdvanceClearingJournalError,
    SupplierAdvanceClearingJournalNotFoundError,
    SupplierAdvanceClearingJournalSourceStateError,
    generate_and_post_supplier_advance_clearing_journal_entry,
    get_original_supplier_advance_clearing_journal_entry,
    reverse_supplier_advance_clearing_journal_entry,
    validate_supplier_advance_clearing_accounting_currency,
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


class QueueDB:
    def __init__(
        self,
        *values,
    ):
        self.values = list(
            values
        )

        self.added = []

    async def execute(
        self,
        statement,
    ):
        if not self.values:
            raise AssertionError(
                "Unexpected db.execute()"
            )

        return ScalarResult(
            self.values.pop(0)
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
        for value in self.added:
            if (
                getattr(
                    value,
                    "id",
                    None,
                )
                is None
            ):
                value.id = 555


def event(
    *,
    event_id=10,
    company_id=1,
    amount=Decimal("120.00"),
    currency_code="UAH",
    clearing_date=D1,
    reversal_of_id=None,
):
    return SimpleNamespace(
        id=event_id,
        company_id=company_id,
        payment_settlement_allocation_id=100,
        invoice_fulfillment_allocation_id=200,
        clearing_date=clearing_date,
        cleared_amount=amount,
        currency_code=currency_code,
        created_by=1,
        reversal_of_id=reversal_of_id,
    )


def test_uah_currency_is_allowed():
    validate_supplier_advance_clearing_accounting_currency(
        event()
    )


def test_non_uah_currency_fails_closed():
    with pytest.raises(
        SupplierAdvanceClearingJournalCurrencyError,
        match="UAH only",
    ):
        validate_supplier_advance_clearing_accounting_currency(
            event(
                currency_code="EUR"
            )
        )


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("0"),
        Decimal("-0.01"),
    ],
)
def test_event_amount_must_be_positive(
    amount,
):
    with pytest.raises(
        SupplierAdvanceClearingJournalSourceStateError,
        match="greater than zero",
    ):
        service._event_amount(
            event(
                amount=amount
            )
        )


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("NaN"),
        Decimal("Infinity"),
    ],
)
def test_event_amount_must_be_finite(
    amount,
):
    with pytest.raises(
        SupplierAdvanceClearingJournalSourceStateError,
        match="finite",
    ):
        service._event_amount(
            event(
                amount=amount
            )
        )


@pytest.mark.asyncio
async def test_reversal_event_cannot_generate_original():
    db = QueueDB()

    with pytest.raises(
        SupplierAdvanceClearingJournalSourceStateError,
        match="reversal",
    ):
        await generate_and_post_supplier_advance_clearing_journal_entry(
            db,
            event=event(
                reversal_of_id=9
            ),
            created_by=1,
        )


@pytest.mark.asyncio
async def test_original_duplicate_is_rejected():
    db = QueueDB(
        999
    )

    with pytest.raises(
        SupplierAdvanceClearingJournalDuplicateError,
        match="already exists",
    ):
        await generate_and_post_supplier_advance_clearing_journal_entry(
            db,
            event=event(),
            created_by=1,
        )


@pytest.mark.asyncio
async def test_original_builds_typed_dr631_cr371_journal(
    monkeypatch,
):
    db = QueueDB(
        None
    )

    async def fake_resolve(
        db,
        *,
        company_id,
        roles,
    ):
        assert company_id == 1

        assert tuple(
            roles
        ) == (
            AccountingAccountRole.SUPPLIER_PAYABLES,
            AccountingAccountRole.SUPPLIER_ADVANCES,
        )

        return {
            AccountingAccountRole.SUPPLIER_PAYABLES:
                SimpleNamespace(
                    id=631
                ),
            AccountingAccountRole.SUPPLIER_ADVANCES:
                SimpleNamespace(
                    id=371
                ),
        }

    captured = {}

    async def fake_validate_and_post(
        db,
        *,
        journal_entry,
    ):
        captured[
            "journal_entry"
        ] = journal_entry

        return journal_entry

    monkeypatch.setattr(
        service,
        "resolve_company_account_roles",
        fake_resolve,
    )

    monkeypatch.setattr(
        service,
        "_validate_and_post",
        fake_validate_and_post,
    )

    result = (
        await generate_and_post_supplier_advance_clearing_journal_entry(
            db,
            event=event(),
            created_by=7,
        )
    )

    entry = captured[
        "journal_entry"
    ]

    assert result is entry

    assert isinstance(
        entry,
        JournalEntry,
    )

    assert entry.company_id == 1

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
        entry.input_vat_fulfillment_bridge_event_id
        is None
    )

    assert (
        entry.supplier_advance_clearing_event_id
        == 10
    )

    assert entry.entry_date == D1

    assert (
        entry.status
        == JournalEntryStatus.DRAFT
    )

    assert entry.created_by == 7

    assert len(
        entry.lines
    ) == 2

    assert (
        entry.lines[0].account_id
        == 631
    )

    assert (
        entry.lines[0].debit
        == Decimal("120.00")
    )

    assert (
        entry.lines[0].credit
        == Decimal("0")
    )

    assert (
        entry.lines[1].account_id
        == 371
    )

    assert (
        entry.lines[1].debit
        == Decimal("0")
    )

    assert (
        entry.lines[1].credit
        == Decimal("120.00")
    )


@pytest.mark.asyncio
async def test_role_resolution_error_is_wrapped(
    monkeypatch,
):
    db = QueueDB(
        None
    )

    async def fail_resolve(
        db,
        *,
        company_id,
        roles,
    ):
        raise AccountingRoleNotConfiguredError(
            "supplier_payables missing"
        )

    monkeypatch.setattr(
        service,
        "resolve_company_account_roles",
        fail_resolve,
    )

    with pytest.raises(
        SupplierAdvanceClearingJournalError,
        match="supplier_payables missing",
    ):
        await generate_and_post_supplier_advance_clearing_journal_entry(
            db,
            event=event(),
            created_by=1,
        )


@pytest.mark.asyncio
async def test_get_original_returns_matching_entry():
    journal = SimpleNamespace(
        id=70
    )

    db = QueueDB(
        journal
    )

    result = (
        await get_original_supplier_advance_clearing_journal_entry(
            db,
            company_id=1,
            supplier_advance_clearing_event_id=10,
        )
    )

    assert result is journal


@pytest.mark.asyncio
async def test_get_original_raises_when_missing():
    db = QueueDB(
        None
    )

    with pytest.raises(
        SupplierAdvanceClearingJournalNotFoundError,
        match="not found",
    ):
        await get_original_supplier_advance_clearing_journal_entry(
            db,
            company_id=1,
            supplier_advance_clearing_event_id=10,
        )


@pytest.mark.asyncio
async def test_reversal_uses_supplier_typed_source_override(
    monkeypatch,
):
    reversal = event(
        event_id=11,
        amount=Decimal("120.00"),
        clearing_date=D2,
        reversal_of_id=10,
    )

    original = SimpleNamespace(
        id=70
    )

    expected = SimpleNamespace(
        id=71
    )

    db = QueueDB(
        None
    )

    async def fake_get_original(
        db,
        *,
        company_id,
        supplier_advance_clearing_event_id,
        lock,
    ):
        assert company_id == 1

        assert (
            supplier_advance_clearing_event_id
            == 10
        )

        assert lock is True

        return original

    captured = {}

    async def fake_reverse(
        *,
        db,
        company_id,
        journal_entry_id,
        reversal_date,
        reversed_by,
        supplier_advance_clearing_event_id_override,
    ):
        captured.update(
            company_id=company_id,
            journal_entry_id=journal_entry_id,
            reversal_date=reversal_date,
            reversed_by=reversed_by,
            override=(
                supplier_advance_clearing_event_id_override
            ),
        )

        return expected

    monkeypatch.setattr(
        service,
        "get_original_supplier_advance_clearing_journal_entry",
        fake_get_original,
    )

    monkeypatch.setattr(
        service,
        "reverse_journal_entry",
        fake_reverse,
    )

    result = (
        await reverse_supplier_advance_clearing_journal_entry(
            db,
            reversal_event=reversal,
            reversed_by=9,
        )
    )

    assert result is expected

    assert captured == {
        "company_id": 1,
        "journal_entry_id": 70,
        "reversal_date": D2,
        "reversed_by": 9,
        "override": 11,
    }


@pytest.mark.asyncio
async def test_reversal_rejects_non_reversal_event():
    db = QueueDB()

    with pytest.raises(
        SupplierAdvanceClearingJournalSourceStateError,
        match="Only a Supplier Advance Clearing reversal",
    ):
        await reverse_supplier_advance_clearing_journal_entry(
            db,
            reversal_event=event(),
            reversed_by=1,
        )


@pytest.mark.asyncio
async def test_reversal_duplicate_is_rejected():
    db = QueueDB(
        999
    )

    with pytest.raises(
        SupplierAdvanceClearingJournalDuplicateError,
        match="already exists",
    ):
        await reverse_supplier_advance_clearing_journal_entry(
            db,
            reversal_event=event(
                event_id=11,
                clearing_date=D2,
                reversal_of_id=10,
            ),
            reversed_by=1,
        )


@pytest.mark.asyncio
async def test_event_requires_persistent_id():
    db = QueueDB()

    with pytest.raises(
        SupplierAdvanceClearingJournalSourceStateError,
        match="persistent positive ID",
    ):
        await generate_and_post_supplier_advance_clearing_journal_entry(
            db,
            event=event(
                event_id=None
            ),
            created_by=1,
        )
