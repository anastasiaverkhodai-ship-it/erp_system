from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.services.sales_recognition_journal_service as service
from app.models.journal_entry import (
    JournalEntryStatus,
)
from app.services.accounting_account_role_resolver import (
    AccountingRoleNotConfiguredError,
)
from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.sales_recognition_journal_service import (
    SalesRecognitionJournalCurrencyError,
    SalesRecognitionJournalDuplicateError,
    SalesRecognitionJournalError,
    SalesRecognitionJournalNotFoundError,
    SalesRecognitionJournalSourceStateError,
    generate_and_post_sales_recognition_journal_entry,
    get_original_sales_recognition_journal_entry,
    reverse_sales_recognition_journal_entry,
    validate_sales_recognition_accounting_currency,
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
        assert self.values

        return ScalarResult(
            self.values.pop(0)
        )


def event_stub(
    *,
    event_id=10,
    company_id=1,
    reversal_of_id=None,
    currency_code="UAH",
    gross=Decimal("120.00"),
):
    return SimpleNamespace(
        id=event_id,
        company_id=company_id,
        invoice_fulfillment_allocation_id=50,
        recognition_date=date(
            2026,
            9,
            1,
        ),
        recognized_quantity=Decimal("1.0000"),
        recognized_gross_amount=gross,
        recognized_tax_amount=Decimal("20.00"),
        currency_code=currency_code,
        created_by=1,
        reversal_of_id=reversal_of_id,
    )


def test_uah_currency_is_allowed():

    validate_sales_recognition_accounting_currency(
        event_stub()
    )


def test_non_uah_currency_fails_closed():

    with pytest.raises(
        SalesRecognitionJournalCurrencyError
    ):
        validate_sales_recognition_accounting_currency(
            event_stub(
                currency_code="EUR"
            )
        )


@pytest.mark.asyncio
async def test_original_event_posts_gross_receivable_and_revenue(
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
            AccountingAccountRole.CUSTOMER_RECEIVABLES,
            AccountingAccountRole.GOODS_REVENUE,
        )

        return {
            AccountingAccountRole.CUSTOMER_RECEIVABLES:
                SimpleNamespace(
                    id=361
                ),
            AccountingAccountRole.GOODS_REVENUE:
                SimpleNamespace(
                    id=702
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
        await generate_and_post_sales_recognition_journal_entry(
            db,
            event=event_stub(),
            created_by=7,
        )
    )

    entry = captured[
        "journal_entry"
    ]

    assert result is entry

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
        == 10
    )

    assert entry.accounting_rule_id is None

    assert (
        entry.entry_date
        == date(
            2026,
            9,
            1,
        )
    )

    assert (
        entry.status
        == JournalEntryStatus.DRAFT
    )

    assert entry.created_by == 7

    assert len(
        entry.lines
    ) == 2

    assert entry.lines[0].account_id == 361
    assert (
        entry.lines[0].debit
        == Decimal("120.00")
    )
    assert (
        entry.lines[0].credit
        == Decimal("0.00")
    )

    assert entry.lines[1].account_id == 702
    assert (
        entry.lines[1].debit
        == Decimal("0.00")
    )
    assert (
        entry.lines[1].credit
        == Decimal("120.00")
    )


@pytest.mark.asyncio
async def test_original_event_duplicate_is_rejected():

    db = QueueDB(
        999
    )

    with pytest.raises(
        SalesRecognitionJournalDuplicateError
    ):
        await generate_and_post_sales_recognition_journal_entry(
            db,
            event=event_stub(),
            created_by=1,
        )


@pytest.mark.asyncio
async def test_reversal_event_cannot_generate_original_je():

    db = QueueDB()

    with pytest.raises(
        SalesRecognitionJournalSourceStateError
    ):
        await generate_and_post_sales_recognition_journal_entry(
            db,
            event=event_stub(
                reversal_of_id=5
            ),
            created_by=1,
        )


@pytest.mark.asyncio
async def test_original_event_requires_valid_creator():

    db = QueueDB()

    with pytest.raises(
        SalesRecognitionJournalSourceStateError
    ):
        await generate_and_post_sales_recognition_journal_entry(
            db,
            event=event_stub(),
            created_by=0,
        )


@pytest.mark.asyncio
async def test_original_event_fails_closed_when_roles_missing(
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
            "goods_revenue missing"
        )

    monkeypatch.setattr(
        service,
        "resolve_company_account_roles",
        fail_resolve,
    )

    with pytest.raises(
        SalesRecognitionJournalError,
        match="goods_revenue missing",
    ):
        await generate_and_post_sales_recognition_journal_entry(
            db,
            event=event_stub(),
            created_by=1,
        )


@pytest.mark.asyncio
async def test_get_original_sales_je_not_found():

    db = QueueDB(
        None
    )

    with pytest.raises(
        SalesRecognitionJournalNotFoundError
    ):
        await get_original_sales_recognition_journal_entry(
            db,
            company_id=1,
            sales_recognition_event_id=10,
        )


@pytest.mark.asyncio
async def test_reversal_event_uses_source_override(
    monkeypatch,
):

    original_je = SimpleNamespace(
        id=700,
    )

    db = QueueDB(
        None,
        original_je,
    )

    captured = {}

    async def fake_reverse(
        *,
        db,
        company_id,
        journal_entry_id,
        reversal_date,
        reversed_by,
        sales_recognition_event_id_override=None,
    ):
        captured.update(
            company_id=company_id,
            journal_entry_id=journal_entry_id,
            reversal_date=reversal_date,
            reversed_by=reversed_by,
            override=(
                sales_recognition_event_id_override
            ),
        )

        return SimpleNamespace(
            id=701
        )

    monkeypatch.setattr(
        service,
        "reverse_journal_entry",
        fake_reverse,
    )

    result = (
        await reverse_sales_recognition_journal_entry(
            db,
            reversal_event=event_stub(
                event_id=20,
                reversal_of_id=10,
            ),
            reversed_by=9,
        )
    )

    assert result.id == 701

    assert captured == {
        "company_id": 1,
        "journal_entry_id": 700,
        "reversal_date": date(
            2026,
            9,
            1,
        ),
        "reversed_by": 9,
        "override": 20,
    }


@pytest.mark.asyncio
async def test_reversal_event_duplicate_is_rejected():

    db = QueueDB(
        701
    )

    with pytest.raises(
        SalesRecognitionJournalDuplicateError
    ):
        await reverse_sales_recognition_journal_entry(
            db,
            reversal_event=event_stub(
                event_id=20,
                reversal_of_id=10,
            ),
            reversed_by=9,
        )


@pytest.mark.asyncio
async def test_original_event_cannot_use_reversal_path():

    db = QueueDB()

    with pytest.raises(
        SalesRecognitionJournalSourceStateError
    ):
        await reverse_sales_recognition_journal_entry(
            db,
            reversal_event=event_stub(
                event_id=20,
                reversal_of_id=None,
            ),
            reversed_by=9,
        )


@pytest.mark.asyncio
async def test_sales_event_requires_persistent_id():

    db = QueueDB()

    with pytest.raises(
        SalesRecognitionJournalSourceStateError
    ):
        await generate_and_post_sales_recognition_journal_entry(
            db,
            event=event_stub(
                event_id=None
            ),
            created_by=1,
        )
