from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.purchase_value_correction_moving_average_journal_service as journal_module
from app.models.journal_entry import (
    JournalEntryStatus,
)
from app.models.purchase_value_correction_moving_average_replay_event import (
    PurchaseValueCorrectionMovingAverageReplayEvent,
)
from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.purchase_value_correction_moving_average_issued_destination_resolver import (
    HistoricalMovingAverageIssuedDestination,
)
from app.services.purchase_value_correction_moving_average_journal_service import (
    PurchaseValueCorrectionMovingAverageJournalCurrencyError,
    PurchaseValueCorrectionMovingAverageJournalDuplicateError,
    PurchaseValueCorrectionMovingAverageJournalSourceStateError,
    generate_and_post_purchase_value_correction_moving_average_journal_entry,
    reverse_purchase_value_correction_moving_average_journal_entry,
)


def D(value: str) -> Decimal:
    return Decimal(
        value
    )


def event(
    *,
    event_id=501,
    effect_kind="on_hand",
    original="600",
    corrected="540",
    reversal_of_id=None,
    recognition_date=date(2026, 9, 5),
):
    row = PurchaseValueCorrectionMovingAverageReplayEvent(
        company_id=1,
        purchase_value_correction_allocation_event_id=10,
        product_id=20,
        warehouse_id=30,
        effect_kind=effect_kind,
        source_moving_average_movement_id=(
            40 if effect_kind == "issued" else None
        ),
        source_inventory_cost_entry_id=(
            50 if effect_kind == "issued" else None
        ),
        recognition_date=recognition_date,
        quantity=D("60.0000"),
        original_valuation_amount=D(original),
        corrected_valuation_amount=D(corrected),
        currency_code="UAH",
        created_by=1,
        reversal_of_id=reversal_of_id,
    )
    row.id = event_id
    return row


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
        value = (
            self.execute_values.pop(0)
            if self.execute_values
            else None
        )
        return ScalarResult(
            value
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

        for item in self.added:
            if getattr(
                item,
                "id",
                None,
            ) is None:
                item.id = 900


@pytest.mark.asyncio
async def test_on_hand_decrease_posts_inventory_and_supplier(
    monkeypatch,
):
    db = FakeDb(
        execute_values=[
            None,
        ]
    )

    accounts = {
        AccountingAccountRole.INVENTORY_GOODS: SimpleNamespace(
            id=281
        ),
        AccountingAccountRole.SUPPLIER_PAYABLES: SimpleNamespace(
            id=631
        ),
    }

    monkeypatch.setattr(
        journal_module,
        "resolve_company_account_roles",
        AsyncMock(
            return_value=accounts
        ),
    )
    monkeypatch.setattr(
        journal_module,
        "validate_journal_entry",
        AsyncMock(
            return_value=None
        ),
    )

    async def fake_post(
        *,
        db,
        company_id,
        journal_entry_id,
    ):
        entry = db.added[-1]
        entry.status = JournalEntryStatus.POSTED
        return entry

    monkeypatch.setattr(
        journal_module,
        "post_journal_entry",
        fake_post,
    )

    source = event()

    entry = (
        await generate_and_post_purchase_value_correction_moving_average_journal_entry(
            db,
            event=source,
            created_by=1,
        )
    )

    assert entry.purchase_value_correction_ma_replay_event_id == 501
    assert entry.purchase_value_correction_fifo_impact_event_id is None
    assert entry.entry_date == date(2026, 9, 5)
    assert entry.status == JournalEntryStatus.POSTED

    assert len(
        entry.lines
    ) == 2

    destination, supplier = entry.lines

    assert destination.account_id == 281
    assert destination.debit == D("0")
    assert destination.credit == D("60")

    assert supplier.account_id == 631
    assert supplier.debit == D("60")
    assert supplier.credit == D("0")


@pytest.mark.asyncio
async def test_issued_decrease_uses_historical_destination(
    monkeypatch,
):
    db = FakeDb(
        execute_values=[
            None,
        ]
    )

    accounts = {
        AccountingAccountRole.SUPPLIER_PAYABLES: SimpleNamespace(
            id=631
        ),
    }

    monkeypatch.setattr(
        journal_module,
        "resolve_company_account_roles",
        AsyncMock(
            return_value=accounts
        ),
    )

    resolver = AsyncMock(
        return_value=HistoricalMovingAverageIssuedDestination(
            account_id=901234,
            original_journal_entry_id=77,
            accounting_rule_id=88,
            moving_average_movement_id=40,
            inventory_cost_entry_id=50,
        )
    )

    monkeypatch.setattr(
        journal_module,
        "resolve_purchase_value_correction_moving_average_issued_destination",
        resolver,
    )
    monkeypatch.setattr(
        journal_module,
        "validate_journal_entry",
        AsyncMock(
            return_value=None
        ),
    )

    async def fake_post(
        *,
        db,
        company_id,
        journal_entry_id,
    ):
        return db.added[-1]

    monkeypatch.setattr(
        journal_module,
        "post_journal_entry",
        fake_post,
    )

    source = event(
        effect_kind="issued",
        original="400",
        corrected="360",
    )

    entry = (
        await generate_and_post_purchase_value_correction_moving_average_journal_entry(
            db,
            event=source,
            created_by=1,
        )
    )

    destination, supplier = entry.lines

    assert destination.account_id == 901234
    assert destination.credit == D("40")
    assert supplier.account_id == 631
    assert supplier.debit == D("40")

    resolver.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_original_fails():
    db = FakeDb(
        execute_values=[
            999,
        ]
    )

    with pytest.raises(
        PurchaseValueCorrectionMovingAverageJournalDuplicateError
    ):
        await generate_and_post_purchase_value_correction_moving_average_journal_entry(
            db,
            event=event(),
            created_by=1,
        )


@pytest.mark.asyncio
async def test_reversal_event_cannot_generate_original():
    db = FakeDb()

    with pytest.raises(
        PurchaseValueCorrectionMovingAverageJournalSourceStateError
    ):
        await generate_and_post_purchase_value_correction_moving_average_journal_entry(
            db,
            event=event(
                event_id=502,
                original="540",
                corrected="600",
                reversal_of_id=501,
            ),
            created_by=1,
        )


@pytest.mark.asyncio
async def test_non_uah_fails():
    db = FakeDb()

    source = event()
    source.currency_code = "EUR"

    with pytest.raises(
        PurchaseValueCorrectionMovingAverageJournalCurrencyError
    ):
        await generate_and_post_purchase_value_correction_moving_average_journal_entry(
            db,
            event=source,
            created_by=1,
        )


@pytest.mark.asyncio
async def test_reversal_mirrors_original_through_accounting_reversal(
    monkeypatch,
):
    original_entry = SimpleNamespace(
        id=777,
        status=JournalEntryStatus.POSTED,
    )

    db = FakeDb(
        execute_values=[
            None,
            original_entry,
        ]
    )

    reversed_entry = SimpleNamespace(
        id=778,
    )

    reverse_mock = AsyncMock(
        return_value=reversed_entry
    )

    monkeypatch.setattr(
        journal_module,
        "reverse_journal_entry",
        reverse_mock,
    )

    reversal = event(
        event_id=502,
        original="540",
        corrected="600",
        reversal_of_id=501,
        recognition_date=date(2026, 9, 10),
    )

    result = (
        await reverse_purchase_value_correction_moving_average_journal_entry(
            db,
            reversal_event=reversal,
            reversed_by=9,
        )
    )

    assert result is reversed_entry

    reverse_mock.assert_awaited_once_with(
        db=db,
        company_id=1,
        journal_entry_id=777,
        reversal_date=date(2026, 9, 10),
        reversed_by=9,
        purchase_value_correction_ma_replay_event_id_override=502,
    )


@pytest.mark.asyncio
async def test_reversal_duplicate_fails():
    db = FakeDb(
        execute_values=[
            1000,
        ]
    )

    reversal = event(
        event_id=502,
        original="540",
        corrected="600",
        reversal_of_id=501,
    )

    with pytest.raises(
        PurchaseValueCorrectionMovingAverageJournalDuplicateError
    ):
        await reverse_purchase_value_correction_moving_average_journal_entry(
            db,
            reversal_event=reversal,
            reversed_by=1,
        )
