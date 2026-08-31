from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.accounting_posting import (
    AccountingPostingError,
    validate_journal_entry,
)


@pytest.mark.asyncio
async def test_unbalanced_journal_entry_is_rejected():
    db = SimpleNamespace(
        execute=AsyncMock()
    )

    journal_entry = SimpleNamespace(
        company_id=1,
        lines=[
            SimpleNamespace(
                account_id=2,
                debit=Decimal("1000.00"),
                credit=Decimal("0.00"),
            ),
            SimpleNamespace(
                account_id=4,
                debit=Decimal("0.00"),
                credit=Decimal("900.00"),
            ),
        ],
    )

    with pytest.raises(
        AccountingPostingError,
        match="Journal entry is not balanced",
    ):
        await validate_journal_entry(
            db=db,
            journal_entry=journal_entry,
        )

    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_foreign_company_account_is_rejected():
    result = MagicMock()
    result.scalars.return_value.all.return_value = [4]

    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=result
        )
    )

    journal_entry = SimpleNamespace(
        company_id=1,
        lines=[
            SimpleNamespace(
                account_id=4,
                debit=Decimal("1000.00"),
                credit=Decimal("0.00"),
            ),
            SimpleNamespace(
                account_id=5,
                debit=Decimal("0.00"),
                credit=Decimal("1000.00"),
            ),
        ],
    )

    with pytest.raises(
        AccountingPostingError,
        match="foreign-company accounts",
    ):
        await validate_journal_entry(
            db=db,
            journal_entry=journal_entry,
        )

    db.execute.assert_awaited_once()
