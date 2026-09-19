from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.journal_entry import JournalEntryStatus
from app.services.general_ledger_service import (
    REPORTABLE_STATUSES,
    get_account_card,
    get_general_ledger,
)


class FakeScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


class FakeRowsResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    async def execute(self, _statement):
        self.calls += 1
        return self.results.pop(0)


def _journal(*, entry_date, description):
    return SimpleNamespace(
        id=1001,
        entry_date=entry_date,
        description=description,
    )


def _line(*, line_no, debit, credit):
    return SimpleNamespace(
        id=1002,
        line_no=line_no,
        debit=Decimal(debit),
        credit=Decimal(credit),
        description=None,
    )


def _account():
    return SimpleNamespace(
        id=1003,
        code="311",
        name="Current accounts in national currency",
        normal_balance=SimpleNamespace(value="debit"),
    )


def test_reportable_statuses_exclude_draft_and_include_reversed():
    assert JournalEntryStatus.DRAFT not in REPORTABLE_STATUSES
    assert JournalEntryStatus.POSTED in REPORTABLE_STATUSES
    assert JournalEntryStatus.REVERSED in REPORTABLE_STATUSES


@pytest.mark.asyncio
async def test_general_ledger_calculates_opening_movements_and_closing():
    company_id = 1004
    account = _account()

    rows = [
        (
            _journal(
                entry_date=date(2026, 1, 10),
                description="Receipt",
            ),
            _line(
                line_no=1,
                debit="100.00",
                credit="0.00",
            ),
            account,
        ),
        (
            _journal(
                entry_date=date(2026, 1, 11),
                description="Payment",
            ),
            _line(
                line_no=1,
                debit="0.00",
                credit="25.00",
            ),
            account,
        ),
    ]

    session = FakeSession(
        [
            FakeScalarResult(Decimal("50.00")),
            FakeRowsResult(rows),
        ]
    )

    report = await get_general_ledger(
        session,
        company_id=company_id,
        date_from=date(2026, 1, 1),
        date_to=date(2026, 1, 31),
        account_id=account.id,
    )

    assert report.opening_balance == Decimal("50.00")
    assert report.period_debit == Decimal("100.00")
    assert report.period_credit == Decimal("25.00")
    assert report.closing_balance == Decimal("125.00")
    assert len(report.lines) == 2
    assert report.lines[0].running_balance == Decimal("150.00")
    assert report.lines[1].running_balance == Decimal("125.00")


@pytest.mark.asyncio
async def test_general_ledger_rejects_invalid_date_range():
    session = FakeSession([])

    with pytest.raises(
        ValueError,
        match="date_from must be on or before date_to",
    ):
        await get_general_ledger(
            session,
            company_id=1005,
            date_from=date(2026, 2, 1),
            date_to=date(2026, 1, 1),
        )

    assert session.calls == 0


@pytest.mark.asyncio
async def test_general_ledger_rejects_two_account_filters():
    session = FakeSession([])

    with pytest.raises(
        ValueError,
        match="either account_id or account_code",
    ):
        await get_general_ledger(
            session,
            company_id=1006,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            account_id=1007,
            account_code="311",
        )

    assert session.calls == 0


@pytest.mark.asyncio
async def test_account_card_is_company_scoped():
    session = FakeSession(
        [
            FakeScalarResult(None),
        ]
    )

    with pytest.raises(
        ValueError,
        match="Account not found for company",
    ):
        await get_account_card(
            session,
            company_id=1008,
            account_id=1009,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
        )


@pytest.mark.asyncio
async def test_account_card_uses_general_ledger_semantics():
    company_id = 1010
    account = _account()

    session = FakeSession(
        [
            FakeScalarResult(account),
            FakeScalarResult(Decimal("10.00")),
            FakeRowsResult([]),
        ]
    )

    report = await get_account_card(
        session,
        company_id=company_id,
        account_id=account.id,
        date_from=date(2026, 1, 1),
        date_to=date(2026, 1, 31),
    )

    assert report.account_id == account.id
    assert report.account_code == "311"
    assert report.normal_balance == "debit"
    assert report.opening_balance == Decimal("10.00")
    assert report.period_debit == Decimal("0")
    assert report.period_credit == Decimal("0")
    assert report.closing_balance == Decimal("10.00")
