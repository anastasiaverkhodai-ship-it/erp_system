from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.purchase_value_correction_fifo_journal_service as journal_module
from app.models.accounting_rule_line import (
    AccountingAmountSource,
    AccountingRuleSide,
)
from app.models.document import (
    DocumentStatus,
    DocumentType,
)
from app.models.journal_entry import (
    JournalEntryStatus,
)
from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.purchase_value_correction_fifo_issued_destination_resolver import (
    HistoricalIssuedDestination,
    PurchaseValueCorrectionFifoIssuedDestinationJournalError,
    PurchaseValueCorrectionFifoIssuedDestinationRuleError,
    resolve_historical_issued_destination_from_context,
)
from app.services.purchase_value_correction_fifo_journal_service import (
    PurchaseValueCorrectionFifoJournalCurrencyError,
    PurchaseValueCorrectionFifoJournalDuplicateError,
    PurchaseValueCorrectionFifoJournalSourceStateError,
    generate_and_post_purchase_value_correction_fifo_journal_entry,
)


ZERO = Decimal("0")


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


class DummySession:
    def __init__(
        self,
        *results,
    ):
        self.results = list(
            results
        )
        self.added = []

    async def execute(
        self,
        statement,
    ):
        if not self.results:
            raise AssertionError(
                "Unexpected execute() call"
            )

        return ScalarResult(
            self.results.pop(0)
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
        return None


def make_event(
    *,
    destination_kind="on_hand",
    original="120.00",
    corrected="108.00",
    reversal_of_id=None,
):
    issued = (
        destination_kind
        == "issued"
    )

    return SimpleNamespace(
        id=501,
        company_id=1,
        destination_kind=destination_kind,
        stock_lot_consumption_id=(
            7001
            if issued
            else None
        ),
        issue_document_id=(
            8001
            if issued
            else None
        ),
        issue_document_line_id=(
            8101
            if issued
            else None
        ),
        recognition_date=date(
            2026,
            9,
            5,
        ),
        original_base_amount=Decimal(
            original
        ),
        corrected_base_amount=Decimal(
            corrected
        ),
        currency_code="UAH",
        reversal_of_id=reversal_of_id,
    )


def make_historical_context(
    *,
    rule_destination_account=902,
    journal_destination_account=902,
):
    event = make_event(
        destination_kind="issued",
        original="50.00",
        corrected="47.50",
    )

    document = SimpleNamespace(
        id=8001,
        company_id=1,
        document_type=DocumentType.ISSUE,
        status=DocumentStatus.POSTED,
        document_date=date(
            2026,
            9,
            1,
        ),
        accounting_rule_id=77,
    )

    document_line = SimpleNamespace(
        id=8101,
        document_id=8001,
    )

    rule = SimpleNamespace(
        id=77,
        company_id=1,
        document_type=DocumentType.ISSUE,
        lines=(
            SimpleNamespace(
                account_id=rule_destination_account,
                side=AccountingRuleSide.DEBIT,
                amount_source=(
                    AccountingAmountSource.INVENTORY_COST
                ),
            ),
            SimpleNamespace(
                account_id=281,
                side=AccountingRuleSide.CREDIT,
                amount_source=(
                    AccountingAmountSource.INVENTORY_COST
                ),
            ),
        ),
    )

    journal = SimpleNamespace(
        id=9001,
        company_id=1,
        document_id=8001,
        reversal_of_id=None,
        status=JournalEntryStatus.POSTED,
        accounting_rule_id=77,
        entry_date=date(
            2026,
            9,
            1,
        ),
        lines=(
            SimpleNamespace(
                account_id=journal_destination_account,
                debit=Decimal("50.00"),
                credit=ZERO,
            ),
            SimpleNamespace(
                account_id=281,
                debit=ZERO,
                credit=Decimal("50.00"),
            ),
        ),
    )

    return (
        event,
        document,
        document_line,
        journal,
        rule,
    )


def test_historical_issued_destination_uses_original_issue_account():
    (
        event,
        document,
        document_line,
        journal,
        rule,
    ) = make_historical_context()

    result = (
        resolve_historical_issued_destination_from_context(
            event=event,
            issue_document=document,
            issue_line=document_line,
            journal_entry=journal,
            accounting_rule=rule,
        )
    )

    assert result == HistoricalIssuedDestination(
        account_id=902,
        original_journal_entry_id=9001,
        accounting_rule_id=77,
    )


def test_historical_destination_fails_if_rule_and_journal_drift():
    (
        event,
        document,
        document_line,
        journal,
        rule,
    ) = make_historical_context(
        rule_destination_account=903,
        journal_destination_account=902,
    )

    with pytest.raises(
        PurchaseValueCorrectionFifoIssuedDestinationJournalError,
    ):
        resolve_historical_issued_destination_from_context(
            event=event,
            issue_document=document,
            issue_line=document_line,
            journal_entry=journal,
            accounting_rule=rule,
        )


def test_historical_destination_rejects_ambiguous_inventory_cost_debits():
    (
        event,
        document,
        document_line,
        journal,
        rule,
    ) = make_historical_context()

    rule.lines = (
        *rule.lines,
        SimpleNamespace(
            account_id=903,
            side=AccountingRuleSide.DEBIT,
            amount_source=(
                AccountingAmountSource.INVENTORY_COST
            ),
        ),
    )

    with pytest.raises(
        PurchaseValueCorrectionFifoIssuedDestinationRuleError,
        match="exactly one",
    ):
        resolve_historical_issued_destination_from_context(
            event=event,
            issue_document=document,
            issue_line=document_line,
            journal_entry=journal,
            accounting_rule=rule,
        )


@pytest.mark.asyncio
async def test_on_hand_original_posts_dr631_cr281_for_decrease(
    monkeypatch,
):
    event = make_event(
        destination_kind="on_hand",
        original="120.00",
        corrected="108.00",
    )

    db = DummySession(
        None,
    )

    monkeypatch.setattr(
        journal_module,
        "resolve_company_account_roles",
        AsyncMock(
            return_value={
                AccountingAccountRole.SUPPLIER_PAYABLES:
                    SimpleNamespace(
                        id=631,
                    ),
                AccountingAccountRole.INVENTORY_GOODS:
                    SimpleNamespace(
                        id=281,
                    ),
            }
        ),
    )

    captured = {}

    async def fake_validate_and_post(
        db,
        *,
        journal_entry,
    ):
        captured[
            "entry"
        ] = journal_entry

        return journal_entry

    monkeypatch.setattr(
        journal_module,
        "_validate_and_post",
        fake_validate_and_post,
    )

    result = (
        await generate_and_post_purchase_value_correction_fifo_journal_entry(
            db,
            event=event,
            created_by=10,
        )
    )

    assert result is captured["entry"]

    entry = captured["entry"]

    assert (
        entry.purchase_value_correction_fifo_impact_event_id
        == 501
    )

    assert entry.entry_date == date(
        2026,
        9,
        5,
    )

    assert entry.accounting_rule_id is None

    by_account = {
        line.account_id: line
        for line in entry.lines
    }

    assert by_account[
        631
    ].debit == Decimal(
        "12.00"
    )

    assert by_account[
        631
    ].credit == ZERO

    assert by_account[
        281
    ].debit == ZERO

    assert by_account[
        281
    ].credit == Decimal(
        "12.00"
    )


@pytest.mark.asyncio
async def test_issued_original_uses_historical_destination_not_current_cogs(
    monkeypatch,
):
    event = make_event(
        destination_kind="issued",
        original="50.00",
        corrected="47.50",
    )

    db = DummySession(
        None,
    )

    monkeypatch.setattr(
        journal_module,
        "resolve_company_account_roles",
        AsyncMock(
            return_value={
                AccountingAccountRole.SUPPLIER_PAYABLES:
                    SimpleNamespace(
                        id=631,
                    ),
            }
        ),
    )

    historical_resolver = AsyncMock(
        return_value=(
            HistoricalIssuedDestination(
                account_id=902,
                original_journal_entry_id=9001,
                accounting_rule_id=77,
            )
        )
    )

    monkeypatch.setattr(
        journal_module,
        "resolve_purchase_value_correction_fifo_issued_destination",
        historical_resolver,
    )

    captured = {}

    async def fake_validate_and_post(
        db,
        *,
        journal_entry,
    ):
        captured[
            "entry"
        ] = journal_entry

        return journal_entry

    monkeypatch.setattr(
        journal_module,
        "_validate_and_post",
        fake_validate_and_post,
    )

    await generate_and_post_purchase_value_correction_fifo_journal_entry(
        db,
        event=event,
        created_by=10,
    )

    historical_resolver.assert_awaited_once()

    entry = captured["entry"]

    by_account = {
        line.account_id: line
        for line in entry.lines
    }

    assert by_account[
        631
    ].debit == Decimal(
        "2.50"
    )

    assert by_account[
        631
    ].credit == ZERO

    assert by_account[
        902
    ].debit == ZERO

    assert by_account[
        902
    ].credit == Decimal(
        "2.50"
    )

    assert 281 not in by_account


@pytest.mark.asyncio
async def test_issued_price_increase_debits_historical_destination(
    monkeypatch,
):
    event = make_event(
        destination_kind="issued",
        original="50.00",
        corrected="55.00",
    )

    db = DummySession(
        None,
    )

    monkeypatch.setattr(
        journal_module,
        "resolve_company_account_roles",
        AsyncMock(
            return_value={
                AccountingAccountRole.SUPPLIER_PAYABLES:
                    SimpleNamespace(
                        id=631,
                    ),
            }
        ),
    )

    monkeypatch.setattr(
        journal_module,
        "resolve_purchase_value_correction_fifo_issued_destination",
        AsyncMock(
            return_value=(
                HistoricalIssuedDestination(
                    account_id=902,
                    original_journal_entry_id=9001,
                    accounting_rule_id=77,
                )
            )
        ),
    )

    captured = {}

    async def fake_validate_and_post(
        db,
        *,
        journal_entry,
    ):
        captured["entry"] = journal_entry
        return journal_entry

    monkeypatch.setattr(
        journal_module,
        "_validate_and_post",
        fake_validate_and_post,
    )

    await generate_and_post_purchase_value_correction_fifo_journal_entry(
        db,
        event=event,
        created_by=10,
    )

    by_account = {
        line.account_id: line
        for line in captured["entry"].lines
    }

    assert by_account[
        902
    ].debit == Decimal(
        "5.00"
    )

    assert by_account[
        902
    ].credit == ZERO

    assert by_account[
        631
    ].debit == ZERO

    assert by_account[
        631
    ].credit == Decimal(
        "5.00"
    )


@pytest.mark.asyncio
async def test_original_duplicate_is_rejected():
    event = make_event()

    db = DummySession(
        999,
    )

    with pytest.raises(
        PurchaseValueCorrectionFifoJournalDuplicateError,
    ):
        await generate_and_post_purchase_value_correction_fifo_journal_entry(
            db,
            event=event,
            created_by=10,
        )


@pytest.mark.asyncio
async def test_reversal_event_cannot_generate_original():
    event = make_event(
        reversal_of_id=400,
    )

    db = DummySession()

    with pytest.raises(
        PurchaseValueCorrectionFifoJournalSourceStateError,
        match="reversal event",
    ):
        await generate_and_post_purchase_value_correction_fifo_journal_entry(
            db,
            event=event,
            created_by=10,
        )


@pytest.mark.asyncio
async def test_non_uah_currency_fails_closed():
    event = make_event()

    event.currency_code = "EUR"

    db = DummySession()

    with pytest.raises(
        PurchaseValueCorrectionFifoJournalCurrencyError,
        match="UAH",
    ):
        await generate_and_post_purchase_value_correction_fifo_journal_entry(
            db,
            event=event,
            created_by=10,
        )


@pytest.mark.asyncio
async def test_reversal_uses_generic_reversal_with_fifo_typed_override(
    monkeypatch,
):
    reversal_event = make_event(
        destination_kind="issued",
        original="50.00",
        corrected="47.50",
        reversal_of_id=400,
    )

    db = DummySession(
        None,
        SimpleNamespace(
            id=9901,
        ),
    )

    reverse_mock = AsyncMock(
        return_value=SimpleNamespace(
            id=9902,
        )
    )

    monkeypatch.setattr(
        journal_module,
        "reverse_journal_entry",
        reverse_mock,
    )

    result = (
        await journal_module.reverse_purchase_value_correction_fifo_journal_entry(
            db,
            reversal_event=reversal_event,
            reversed_by=10,
        )
    )

    assert result.id == 9902

    reverse_mock.assert_awaited_once_with(
        db=db,
        company_id=1,
        journal_entry_id=9901,
        reversal_date=date(
            2026,
            9,
            5,
        ),
        reversed_by=10,
        purchase_value_correction_fifo_impact_event_id_override=501,
    )


@pytest.mark.asyncio
async def test_reversal_duplicate_is_rejected():
    reversal_event = make_event(
        reversal_of_id=400,
    )

    db = DummySession(
        999,
    )

    with pytest.raises(
        PurchaseValueCorrectionFifoJournalDuplicateError,
        match="already exists",
    ):
        await journal_module.reverse_purchase_value_correction_fifo_journal_entry(
            db,
            reversal_event=reversal_event,
            reversed_by=10,
        )


@pytest.mark.asyncio
async def test_non_reversal_cannot_call_reversal_service():
    event = make_event()

    db = DummySession()

    with pytest.raises(
        PurchaseValueCorrectionFifoJournalSourceStateError,
        match="reversal event",
    ):
        await journal_module.reverse_purchase_value_correction_fifo_journal_entry(
            db,
            reversal_event=event,
            reversed_by=10,
        )
