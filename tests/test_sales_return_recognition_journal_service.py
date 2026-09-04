from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.sales_return_recognition_journal_service as service

from app.models.journal_entry import (
    JournalEntryStatus,
)
from app.models.journal_entry_line import (
    JournalEntryLine,
)
from app.models.sales_return_recognition_event import (
    SalesReturnRecognitionEvent,
)
from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.sales_return_recognition_accounting_service import (
    create_sales_return_recognition_accounting_plan,
)
from app.services.sales_return_recognition_journal_service import (
    SalesReturnRecognitionJournalCurrencyError,
    SalesReturnRecognitionJournalDuplicateError,
    SalesReturnRecognitionJournalNotFoundError,
    SalesReturnRecognitionJournalSourceStateError,
    _build_journal_lines,
    generate_and_post_sales_return_recognition_journal_entry,
    get_original_sales_return_recognition_journal_entry,
    reverse_sales_return_recognition_journal_entry,
    validate_sales_return_recognition_accounting_currency,
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


def event(
    *,
    event_id=10,
    recognition_date=D1,
    gross="120.00",
    tax="20.00",
    currency="UAH",
    reversal_of_id=None,
):
    return SalesReturnRecognitionEvent(
        id=event_id,
        company_id=1,
        trade_return_event_id=100,
        sales_recognition_event_id=200,
        recognition_date=recognition_date,
        returned_quantity=Decimal(
            "2"
        ),
        returned_gross_amount=Decimal(
            gross
        ),
        returned_tax_amount=Decimal(
            tax
        ),
        currency_code=currency,
        created_by=7,
        reversal_of_id=(
            reversal_of_id
        ),
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

    async def execute(
        self,
        statement,
    ):
        if not self.values:
            raise AssertionError(
                "Unexpected DB execute"
            )

        return ScalarResult(
            self.values.pop(
                0
            )
        )


def journal_lines():
    return [
        JournalEntryLine(
            line_no=1,
            account_id=7040,
            debit=Decimal(
                "120.00"
            ),
            credit=Decimal(
                "0"
            ),
            description="return",
        ),
        JournalEntryLine(
            line_no=2,
            account_id=3610,
            debit=Decimal(
                "0"
            ),
            credit=Decimal(
                "120.00"
            ),
            description="return",
        ),
    ]


def test_uah_currency_supported():
    validate_sales_return_recognition_accounting_currency(
        event()
    )


def test_non_uah_currency_rejected():
    with pytest.raises(
        SalesReturnRecognitionJournalCurrencyError
    ):
        validate_sales_return_recognition_accounting_currency(
            event(
                currency="EUR"
            )
        )


@pytest.mark.asyncio
async def test_build_lines_resolves_704_and_361_roles(
    monkeypatch,
):
    plan = (
        create_sales_return_recognition_accounting_plan(
            amount=Decimal(
                "120.00"
            )
        )
    )

    captured = {}

    async def fake_resolve(
        db,
        *,
        company_id,
        roles,
    ):
        captured[
            "company_id"
        ] = company_id

        captured[
            "roles"
        ] = roles

        return {
            AccountingAccountRole.SALES_DEDUCTIONS:
                SimpleNamespace(
                    id=7040
                ),
            AccountingAccountRole.CUSTOMER_RECEIVABLES:
                SimpleNamespace(
                    id=3610
                ),
        }

    monkeypatch.setattr(
        service,
        "resolve_company_account_roles",
        fake_resolve,
    )

    lines = await _build_journal_lines(
        object(),
        company_id=1,
        plan=plan,
        description="Sales Return",
    )

    assert captured == {
        "company_id": 1,
        "roles": (
            AccountingAccountRole.SALES_DEDUCTIONS,
            AccountingAccountRole.CUSTOMER_RECEIVABLES,
        ),
    }

    assert len(
        lines
    ) == 2

    assert (
        lines[0].account_id
        == 7040
    )

    assert (
        lines[0].debit
        == Decimal(
            "120.00"
        )
    )

    assert (
        lines[0].credit
        == Decimal(
            "0.00"
        )
    )

    assert (
        lines[1].account_id
        == 3610
    )

    assert (
        lines[1].debit
        == Decimal(
            "0.00"
        )
    )

    assert (
        lines[1].credit
        == Decimal(
            "120.00"
        )
    )


@pytest.mark.asyncio
async def test_generate_original_uses_typed_source_and_gross_amount(
    monkeypatch,
):
    db = QueueDB(
        None
    )

    value = event(
        gross="120.00",
        tax="20.00",
    )

    captured = {}

    original_plan_builder = (
        service
        .create_sales_return_recognition_accounting_plan
    )

    def fake_plan(
        *,
        amount,
    ):
        captured[
            "amount"
        ] = amount

        return original_plan_builder(
            amount=amount
        )

    monkeypatch.setattr(
        service,
        "create_sales_return_recognition_accounting_plan",
        fake_plan,
    )

    monkeypatch.setattr(
        service,
        "_build_journal_lines",
        AsyncMock(
            return_value=journal_lines()
        ),
    )

    posted = SimpleNamespace(
        id=900
    )

    validate_and_post = AsyncMock(
        return_value=posted
    )

    monkeypatch.setattr(
        service,
        "_validate_and_post",
        validate_and_post,
    )

    result = (
        await generate_and_post_sales_return_recognition_journal_entry(
            db,
            event=value,
            created_by=7,
        )
    )

    assert result is posted

    assert (
        captured[
            "amount"
        ]
        == Decimal(
            "120.00"
        )
    )

    call = (
        validate_and_post
        .await_args
    )

    entry = call.kwargs[
        "journal_entry"
    ]

    assert (
        entry.sales_return_recognition_event_id
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
        entry.input_vat_fulfillment_bridge_event_id
        is None
    )

    assert (
        entry.supplier_advance_clearing_event_id
        is None
    )

    assert (
        entry.customer_advance_clearing_event_id
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

    assert entry.created_by == 7

    assert len(
        entry.lines
    ) == 2


@pytest.mark.asyncio
async def test_original_generation_rejects_reversal_event():
    db = QueueDB()

    with pytest.raises(
        SalesReturnRecognitionJournalSourceStateError,
        match="reversal event",
    ):
        await generate_and_post_sales_return_recognition_journal_entry(
            db,
            event=event(
                event_id=11,
                reversal_of_id=10,
            ),
            created_by=7,
        )


@pytest.mark.asyncio
async def test_original_generation_rejects_duplicate():
    db = QueueDB(
        900
    )

    with pytest.raises(
        SalesReturnRecognitionJournalDuplicateError,
        match="already exists",
    ):
        await generate_and_post_sales_return_recognition_journal_entry(
            db,
            event=event(),
            created_by=7,
        )


@pytest.mark.asyncio
async def test_original_generation_requires_positive_created_by():
    db = QueueDB()

    with pytest.raises(
        SalesReturnRecognitionJournalSourceStateError,
        match="created_by",
    ):
        await generate_and_post_sales_return_recognition_journal_entry(
            db,
            event=event(),
            created_by=0,
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
        await get_original_sales_return_recognition_journal_entry(
            db,
            company_id=1,
            sales_return_recognition_event_id=10,
        )
    )

    assert result is journal


@pytest.mark.asyncio
async def test_get_original_raises_when_missing():
    db = QueueDB(
        None
    )

    with pytest.raises(
        SalesReturnRecognitionJournalNotFoundError,
        match="not found",
    ):
        await get_original_sales_return_recognition_journal_entry(
            db,
            company_id=1,
            sales_return_recognition_event_id=10,
        )


@pytest.mark.asyncio
async def test_reversal_requires_reversal_event():
    db = QueueDB()

    with pytest.raises(
        SalesReturnRecognitionJournalSourceStateError,
        match="Only a Sales Return Recognition reversal event",
    ):
        await reverse_sales_return_recognition_journal_entry(
            db,
            reversal_event=event(),
            reversed_by=7,
        )


@pytest.mark.asyncio
async def test_reversal_requires_positive_reversed_by():
    db = QueueDB()

    with pytest.raises(
        SalesReturnRecognitionJournalSourceStateError,
        match="reversed_by",
    ):
        await reverse_sales_return_recognition_journal_entry(
            db,
            reversal_event=event(
                event_id=11,
                recognition_date=D2,
                reversal_of_id=10,
            ),
            reversed_by=0,
        )


@pytest.mark.asyncio
async def test_reversal_duplicate_is_rejected():
    db = QueueDB(
        901
    )

    with pytest.raises(
        SalesReturnRecognitionJournalDuplicateError,
        match="already exists",
    ):
        await reverse_sales_return_recognition_journal_entry(
            db,
            reversal_event=event(
                event_id=11,
                recognition_date=D2,
                reversal_of_id=10,
            ),
            reversed_by=7,
        )


@pytest.mark.asyncio
async def test_reversal_uses_sales_return_typed_source_override(
    monkeypatch,
):
    db = QueueDB(
        None
    )

    reversal = event(
        event_id=11,
        recognition_date=D2,
        reversal_of_id=10,
    )

    original = SimpleNamespace(
        id=70
    )

    expected = SimpleNamespace(
        id=71
    )

    get_original = AsyncMock(
        return_value=original
    )

    monkeypatch.setattr(
        service,
        "get_original_sales_return_recognition_journal_entry",
        get_original,
    )

    captured = {}

    async def fake_reverse(
        *,
        db,
        company_id,
        journal_entry_id,
        reversal_date,
        reversed_by,
        sales_return_recognition_event_id_override,
    ):
        captured.update(
            company_id=company_id,
            journal_entry_id=journal_entry_id,
            reversal_date=reversal_date,
            reversed_by=reversed_by,
            override=(
                sales_return_recognition_event_id_override
            ),
        )

        return expected

    monkeypatch.setattr(
        service,
        "reverse_journal_entry",
        fake_reverse,
    )

    result = (
        await reverse_sales_return_recognition_journal_entry(
            db,
            reversal_event=reversal,
            reversed_by=9,
        )
    )

    assert result is expected

    get_original.assert_awaited_once_with(
        db,
        company_id=1,
        sales_return_recognition_event_id=10,
        lock=True,
    )

    assert captured == {
        "company_id": 1,
        "journal_entry_id": 70,
        "reversal_date": D2,
        "reversed_by": 9,
        "override": 11,
    }


def test_journal_service_does_not_post_tax_component():
    import inspect

    source = inspect.getsource(
        service
        .generate_and_post_sales_return_recognition_journal_entry
    )

    assert (
        "event.returned_gross_amount"
        in source
    )

    assert (
        "event.returned_tax_amount"
        not in source
    )

    assert (
        "VAT_OUTPUT"
        not in source
    )
