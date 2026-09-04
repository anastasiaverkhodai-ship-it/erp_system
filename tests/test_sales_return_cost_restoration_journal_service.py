from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.sales_return_cost_restoration_journal_service as service

from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.models.journal_entry_line import (
    JournalEntryLine,
)
from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.sales_return_cost_restoration_accounting_service import (
    create_sales_return_cost_restoration_accounting_plan,
)
from app.services.sales_return_cost_restoration_journal_service import (
    SalesReturnCostRestorationJournalDuplicateError,
    SalesReturnCostRestorationJournalNotFoundError,
    SalesReturnCostRestorationJournalSourceStateError,
    _build_journal_lines,
    generate_and_post_sales_return_cost_restoration_journal_entry,
    get_original_sales_return_cost_restoration_journal_entry,
    reverse_sales_return_cost_restoration_journal_entry,
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
        assert self.values

        return ScalarResult(
            self.values.pop(
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
        return None


def event(
    *,
    event_id=10,
    company_id=1,
    restoration_date=D1,
    amount="120.00",
    reversal_of_id=None,
):
    return SimpleNamespace(
        id=event_id,
        company_id=company_id,
        trade_return_event_id=100,
        inventory_cost_entry_id=200,
        restoration_date=restoration_date,
        valuation_method="fifo",
        restored_quantity=Decimal(
            "1"
        ),
        restored_valuation_amount=Decimal(
            amount
        ),
        restored_cost_amount=Decimal(
            amount
        ),
        aggregate_historical_unit_cost=Decimal(
            amount
        ),
        reversal_of_id=(
            reversal_of_id
        ),
    )


@pytest.mark.asyncio
async def test_build_lines_resolves_inventory_and_cogs_roles(
    monkeypatch,
):
    accounts = {
        AccountingAccountRole.INVENTORY_GOODS:
            SimpleNamespace(
                id=281
            ),
        AccountingAccountRole.GOODS_COGS:
            SimpleNamespace(
                id=902
            ),
    }

    resolver = AsyncMock(
        return_value=accounts
    )

    monkeypatch.setattr(
        service,
        "resolve_company_account_roles",
        resolver,
    )

    plan = (
        create_sales_return_cost_restoration_accounting_plan(
            Decimal(
                "120.00"
            )
        )
    )

    lines = await _build_journal_lines(
        object(),
        company_id=1,
        plan=plan,
        description="return cogs",
    )

    assert len(
        lines
    ) == 2

    assert (
        lines[0].account_id
        == 281
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
            "0"
        )
    )

    assert (
        lines[1].account_id
        == 902
    )

    assert (
        lines[1].debit
        == Decimal(
            "0"
        )
    )

    assert (
        lines[1].credit
        == Decimal(
            "120.00"
        )
    )


@pytest.mark.asyncio
async def test_original_journal_uses_only_cost_restoration_source(
    monkeypatch,
):
    db = QueueDB(
        None
    )

    expected_lines = [
        JournalEntryLine(
            line_no=1,
            account_id=281,
            debit=Decimal(
                "120.00"
            ),
            credit=Decimal(
                "0"
            ),
        ),
        JournalEntryLine(
            line_no=2,
            account_id=902,
            debit=Decimal(
                "0"
            ),
            credit=Decimal(
                "120.00"
            ),
        ),
    ]

    monkeypatch.setattr(
        service,
        "_build_journal_lines",
        AsyncMock(
            return_value=expected_lines
        ),
    )

    async def fake_post(
        db,
        *,
        journal_entry,
    ):
        return journal_entry

    monkeypatch.setattr(
        service,
        "_validate_and_post",
        fake_post,
    )

    result = (
        await generate_and_post_sales_return_cost_restoration_journal_entry(
            db,
            event=event(),
            created_by=7,
        )
    )

    assert isinstance(
        result,
        JournalEntry,
    )

    assert (
        result.sales_return_cost_restoration_event_id
        == 10
    )

    assert (
        result.sales_return_recognition_event_id
        is None
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

    assert (
        result.vat_advance_bridge_event_id
        is None
    )

    assert (
        result.input_vat_fulfillment_bridge_event_id
        is None
    )

    assert (
        result.supplier_advance_clearing_event_id
        is None
    )

    assert (
        result.customer_advance_clearing_event_id
        is None
    )

    assert result.entry_date == D1

    assert (
        result.status
        == JournalEntryStatus.DRAFT
    )

    assert result.created_by == 7

    assert result.lines == expected_lines


@pytest.mark.asyncio
async def test_original_reversal_event_is_rejected():
    db = QueueDB()

    with pytest.raises(
        SalesReturnCostRestorationJournalSourceStateError,
        match="reversal event",
    ):
        await generate_and_post_sales_return_cost_restoration_journal_entry(
            db,
            event=event(
                event_id=11,
                reversal_of_id=10,
            ),
            created_by=7,
        )


@pytest.mark.asyncio
async def test_duplicate_original_journal_is_rejected():
    db = QueueDB(
        900
    )

    with pytest.raises(
        SalesReturnCostRestorationJournalDuplicateError,
        match="already exists",
    ):
        await generate_and_post_sales_return_cost_restoration_journal_entry(
            db,
            event=event(),
            created_by=7,
        )


@pytest.mark.asyncio
async def test_zero_cost_original_creates_no_gl_entry(
    monkeypatch,
):
    db = QueueDB(
        None
    )

    build = AsyncMock()

    monkeypatch.setattr(
        service,
        "_build_journal_lines",
        build,
    )

    result = (
        await generate_and_post_sales_return_cost_restoration_journal_entry(
            db,
            event=event(
                amount="0.00"
            ),
            created_by=7,
        )
    )

    assert result is None

    build.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_original_returns_matching_entry():
    expected = SimpleNamespace(
        id=70
    )

    db = QueueDB(
        expected
    )

    result = (
        await get_original_sales_return_cost_restoration_journal_entry(
            db,
            company_id=1,
            sales_return_cost_restoration_event_id=10,
        )
    )

    assert result is expected


@pytest.mark.asyncio
async def test_get_original_missing_is_error():
    db = QueueDB(
        None
    )

    with pytest.raises(
        SalesReturnCostRestorationJournalNotFoundError
    ):
        await get_original_sales_return_cost_restoration_journal_entry(
            db,
            company_id=1,
            sales_return_cost_restoration_event_id=10,
        )


@pytest.mark.asyncio
async def test_reversal_uses_typed_source_override(
    monkeypatch,
):
    db = QueueDB(
        None
    )

    original = SimpleNamespace(
        id=70
    )

    monkeypatch.setattr(
        service,
        "get_original_sales_return_cost_restoration_journal_entry",
        AsyncMock(
            return_value=original
        ),
    )

    expected = SimpleNamespace(
        id=71
    )

    captured = {}

    async def fake_reverse(
        db,
        company_id,
        journal_entry_id,
        reversal_date,
        reversed_by,
        **kwargs,
    ):
        captured.update(
            {
                "company_id": company_id,
                "journal_entry_id": journal_entry_id,
                "reversal_date": reversal_date,
                "reversed_by": reversed_by,
                "override": kwargs[
                    "sales_return_cost_restoration_event_id_override"
                ],
            }
        )

        return expected

    monkeypatch.setattr(
        service,
        "reverse_journal_entry",
        fake_reverse,
    )

    result = (
        await reverse_sales_return_cost_restoration_journal_entry(
            db,
            reversal_event=event(
                event_id=11,
                restoration_date=D2,
                reversal_of_id=10,
            ),
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
async def test_non_reversal_event_cannot_reverse_gl():
    db = QueueDB()

    with pytest.raises(
        SalesReturnCostRestorationJournalSourceStateError,
        match="Only a Sales Return",
    ):
        await reverse_sales_return_cost_restoration_journal_entry(
            db,
            reversal_event=event(),
            reversed_by=9,
        )


@pytest.mark.asyncio
async def test_duplicate_reversal_journal_is_rejected():
    db = QueueDB(
        999
    )

    with pytest.raises(
        SalesReturnCostRestorationJournalDuplicateError,
        match="already exists",
    ):
        await reverse_sales_return_cost_restoration_journal_entry(
            db,
            reversal_event=event(
                event_id=11,
                restoration_date=D2,
                reversal_of_id=10,
            ),
            reversed_by=9,
        )


@pytest.mark.asyncio
async def test_zero_cost_reversal_creates_no_gl_entry(
    monkeypatch,
):
    db = QueueDB(
        None
    )

    get_original = AsyncMock()

    reverse = AsyncMock()

    monkeypatch.setattr(
        service,
        "get_original_sales_return_cost_restoration_journal_entry",
        get_original,
    )

    monkeypatch.setattr(
        service,
        "reverse_journal_entry",
        reverse,
    )

    result = (
        await reverse_sales_return_cost_restoration_journal_entry(
            db,
            reversal_event=event(
                event_id=11,
                restoration_date=D2,
                amount="0.00",
                reversal_of_id=10,
            ),
            reversed_by=9,
        )
    )

    assert result is None

    get_original.assert_not_awaited()

    reverse.assert_not_awaited()
