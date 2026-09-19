from datetime import date
from decimal import Decimal

import pytest

from app.models.account import (
    AccountNormalBalance,
    AccountType,
)
from app.models.journal_entry import JournalEntryStatus
from app.services.trial_balance_service import (
    REPORTABLE_STATUSES,
    _split_raw_balance,
    get_trial_balance,
)


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return FakeResult(self.rows)


class Row:
    def __init__(
        self,
        *,
        account_id,
        code,
        name,
        account_type,
        normal_balance,
        opening_debit_turnover,
        opening_credit_turnover,
        period_debit,
        period_credit,
    ):
        self.id = account_id
        self.code = code
        self.name = name
        self.account_type = account_type
        self.normal_balance = normal_balance
        self.opening_debit_turnover = (
            opening_debit_turnover
        )
        self.opening_credit_turnover = (
            opening_credit_turnover
        )
        self.period_debit = period_debit
        self.period_credit = period_credit


def test_reportable_statuses_are_canonical():
    assert REPORTABLE_STATUSES == (
        JournalEntryStatus.POSTED,
        JournalEntryStatus.REVERSED,
    )


@pytest.mark.parametrize(
    (
        "raw",
        "expected_debit",
        "expected_credit",
    ),
    [
        (
            Decimal("100"),
            Decimal("100"),
            Decimal("0"),
        ),
        (
            Decimal("-100"),
            Decimal("0"),
            Decimal("100"),
        ),
        (
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
        ),
    ],
)
def test_split_raw_balance(
    raw,
    expected_debit,
    expected_credit,
):
    assert _split_raw_balance(raw) == (
        expected_debit,
        expected_credit,
    )


@pytest.mark.asyncio
async def test_trial_balance_rejects_invalid_dates():
    session = FakeSession([])

    with pytest.raises(
        ValueError,
        match="date_from",
    ):
        await get_trial_balance(
            session,
            company_id=3,
            date_from=date(2026, 9, 2),
            date_to=date(2026, 9, 1),
        )


@pytest.mark.asyncio
async def test_trial_balance_empty_report():
    session = FakeSession([])

    report = await get_trial_balance(
        session,
        company_id=3,
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 31),
    )

    assert report.company_id == 3
    assert report.lines == []

    assert report.total_opening_debit == 0
    assert report.total_opening_credit == 0
    assert report.total_period_debit == 0
    assert report.total_period_credit == 0
    assert report.total_closing_debit == 0
    assert report.total_closing_credit == 0


@pytest.mark.asyncio
async def test_trial_balance_builds_debit_and_credit_saldo():
    session = FakeSession(
        [
            Row(
                account_id=12,
                code="281",
                name="Товари на складі",
                account_type=AccountType.ASSET,
                normal_balance=(
                    AccountNormalBalance.DEBIT
                ),
                opening_debit_turnover=Decimal(
                    "100"
                ),
                opening_credit_turnover=Decimal(
                    "20"
                ),
                period_debit=Decimal("50"),
                period_credit=Decimal("10"),
            ),
            Row(
                account_id=31,
                code="631",
                name=(
                    "Розрахунки з "
                    "вітчизняними постачальниками"
                ),
                account_type=AccountType.LIABILITY,
                normal_balance=(
                    AccountNormalBalance.CREDIT
                ),
                opening_debit_turnover=Decimal(
                    "20"
                ),
                opening_credit_turnover=Decimal(
                    "100"
                ),
                period_debit=Decimal("10"),
                period_credit=Decimal("50"),
            ),
        ]
    )

    report = await get_trial_balance(
        session,
        company_id=3,
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 31),
    )

    assert len(report.lines) == 2

    debit_line = report.lines[0]
    credit_line = report.lines[1]

    assert debit_line.opening_debit == Decimal(
        "80"
    )
    assert debit_line.opening_credit == 0
    assert debit_line.period_debit == Decimal(
        "50"
    )
    assert debit_line.period_credit == Decimal(
        "10"
    )
    assert debit_line.closing_debit == Decimal(
        "120"
    )
    assert debit_line.closing_credit == 0

    assert credit_line.opening_debit == 0
    assert credit_line.opening_credit == Decimal(
        "80"
    )
    assert credit_line.period_debit == Decimal(
        "10"
    )
    assert credit_line.period_credit == Decimal(
        "50"
    )
    assert credit_line.closing_debit == 0
    assert credit_line.closing_credit == Decimal(
        "120"
    )

    assert report.total_opening_debit == Decimal(
        "80"
    )
    assert report.total_opening_credit == Decimal(
        "80"
    )
    assert report.total_period_debit == Decimal(
        "60"
    )
    assert report.total_period_credit == Decimal(
        "60"
    )
    assert report.total_closing_debit == Decimal(
        "120"
    )
    assert report.total_closing_credit == Decimal(
        "120"
    )


@pytest.mark.asyncio
async def test_trial_balance_compiles_company_status_and_date_contract():
    session = FakeSession([])

    await get_trial_balance(
        session,
        company_id=3,
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 31),
    )

    sql = str(
        session.statement.compile(
            compile_kwargs={
                "literal_binds": True,
            }
        )
    ).lower()

    assert "journal_entries.company_id = 3" in sql
    assert "accounts.company_id = 3" in sql

    assert "journal_entries.status in" in sql
    assert "'posted'" in sql
    assert "'reversed'" in sql

    assert "journal_entries.entry_date" in sql
    assert "<= '2026-08-31'" in sql

    assert "accounts.parent_id" not in sql
    assert "accounts.is_postable" not in sql


@pytest.mark.asyncio
async def test_trial_balance_does_not_hierarchy_roll_up():
    session = FakeSession(
        [
            Row(
                account_id=2,
                code="281",
                name="Товари на складі",
                account_type=AccountType.ASSET,
                normal_balance=(
                    AccountNormalBalance.DEBIT
                ),
                opening_debit_turnover=Decimal(
                    "0"
                ),
                opening_credit_turnover=Decimal(
                    "0"
                ),
                period_debit=Decimal("100"),
                period_credit=Decimal("40"),
            ),
        ]
    )

    report = await get_trial_balance(
        session,
        company_id=1,
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 31),
    )

    assert [
        line.account_code
        for line in report.lines
    ] == ["281"]

    assert report.total_period_debit == Decimal(
        "100"
    )
    assert report.total_period_credit == Decimal(
        "40"
    )
