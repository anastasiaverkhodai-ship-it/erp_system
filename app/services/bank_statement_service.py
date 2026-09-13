from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank_account import BankAccount
from app.models.bank_statement import BankStatement
from app.models.bank_statement_line import (
    BankStatementLine,
)


class BankStatementError(Exception):
    pass


class BankStatementAccountError(
    BankStatementError
):
    pass


class BankStatementDuplicateError(
    BankStatementError
):
    pass


class BankStatementNotFoundError(
    BankStatementError
):
    pass


class BankStatementLineDuplicateError(
    BankStatementError
):
    pass


class BankStatementLineAmountError(
    BankStatementError
):
    pass


def normalize_bank_statement_external_id(
    value: str,
) -> str:
    normalized = value.strip()

    if not normalized:
        raise BankStatementError(
            "Statement external id cannot be blank"
        )

    return normalized


def normalize_bank_statement_line_external_id(
    value: str,
) -> str:
    normalized = value.strip()

    if not normalized:
        raise BankStatementError(
            "Statement line external id "
            "cannot be blank"
        )

    return normalized


def normalize_bank_statement_currency_code(
    value: str,
) -> str:
    normalized = value.strip().upper()

    if len(normalized) != 3:
        raise BankStatementError(
            "Currency code must contain "
            "exactly 3 characters"
        )

    return normalized


async def _require_active_bank_account(
    db: AsyncSession,
    *,
    company_id: int,
    bank_account_id: int,
) -> BankAccount:
    bank_account = (
        await db.execute(
            select(BankAccount).where(
                BankAccount.id
                == bank_account_id,
                BankAccount.company_id
                == company_id,
                BankAccount.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()

    if bank_account is None:
        raise BankStatementAccountError(
            "Active bank account not found "
            "for this company"
        )

    return bank_account


async def create_bank_statement(
    db: AsyncSession,
    *,
    company_id: int,
    bank_account_id: int,
    external_id: str,
    statement_date,
    period_start,
    period_end,
    source_type: str,
    source_reference: str | None,
    created_by: int | None,
) -> BankStatement:
    """
    Caller owns transaction.
    Statement is evidence only.
    """

    await _require_active_bank_account(
        db,
        company_id=company_id,
        bank_account_id=bank_account_id,
    )

    normalized_external_id = (
        normalize_bank_statement_external_id(
            external_id
        )
    )

    existing = (
        await db.execute(
            select(BankStatement.id).where(
                BankStatement.company_id
                == company_id,
                BankStatement.bank_account_id
                == bank_account_id,
                BankStatement.external_id
                == normalized_external_id,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        raise BankStatementDuplicateError(
            "Bank statement already exists"
        )

    statement = BankStatement(
        company_id=company_id,
        bank_account_id=bank_account_id,
        external_id=normalized_external_id,
        statement_date=statement_date,
        period_start=period_start,
        period_end=period_end,
        source_type=source_type.strip(),
        source_reference=source_reference,
        created_by=created_by,
    )

    db.add(statement)
    await db.flush()

    return statement


async def get_bank_statement(
    db: AsyncSession,
    *,
    company_id: int,
    bank_statement_id: int,
) -> BankStatement:
    statement = (
        await db.execute(
            select(BankStatement).where(
                BankStatement.id
                == bank_statement_id,
                BankStatement.company_id
                == company_id,
            )
        )
    ).scalar_one_or_none()

    if statement is None:
        raise BankStatementNotFoundError(
            "Bank statement not found"
        )

    return statement


async def append_bank_statement_line(
    db: AsyncSession,
    *,
    company_id: int,
    bank_statement_id: int,
    external_line_id: str,
    transaction_date,
    value_date,
    amount: Decimal,
    currency_code: str,
    counterparty_name: str | None,
    counterparty_account: str | None,
    payment_reference: str | None,
    description: str | None,
    raw_payload: str | None,
) -> BankStatementLine:
    """
    Append immutable bank evidence.

    No update/delete lifecycle is intentionally provided.
    Caller owns transaction.
    """

    statement = await get_bank_statement(
        db,
        company_id=company_id,
        bank_statement_id=bank_statement_id,
    )

    normalized_external_line_id = (
        normalize_bank_statement_line_external_id(
            external_line_id
        )
    )

    if amount == 0:
        raise BankStatementLineAmountError(
            "Bank statement line amount "
            "cannot be zero"
        )

    normalized_currency = (
        normalize_bank_statement_currency_code(
            currency_code
        )
    )

    existing = (
        await db.execute(
            select(BankStatementLine.id).where(
                BankStatementLine.company_id
                == company_id,
                BankStatementLine.bank_statement_id
                == bank_statement_id,
                BankStatementLine.external_line_id
                == normalized_external_line_id,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        raise BankStatementLineDuplicateError(
            "Bank statement line already exists"
        )

    line = BankStatementLine(
        company_id=company_id,
        bank_statement_id=statement.id,
        bank_account_id=statement.bank_account_id,
        external_line_id=(
            normalized_external_line_id
        ),
        transaction_date=transaction_date,
        value_date=value_date,
        amount=amount,
        currency_code=normalized_currency,
        counterparty_name=counterparty_name,
        counterparty_account=(
            counterparty_account
        ),
        payment_reference=payment_reference,
        description=description,
        raw_payload=raw_payload,
    )

    db.add(line)
    await db.flush()

    return line


async def get_bank_statement_line(
    db: AsyncSession,
    *,
    company_id: int,
    bank_statement_line_id: int,
    lock_for_update: bool = False,
) -> BankStatementLine:
    """
    Return one company-scoped immutable BankStatementLine.

    Caller owns transaction.
    """
    query = select(BankStatementLine).where(
        BankStatementLine.company_id == company_id,
        BankStatementLine.id == bank_statement_line_id,
    )

    if lock_for_update:
        query = query.with_for_update()

    line = (
        await db.execute(query)
    ).scalar_one_or_none()

    if line is None:
        raise BankStatementNotFoundError(
            "Bank statement line not found"
        )

    return line


async def list_bank_statement_lines(
    db: AsyncSession,
    *,
    company_id: int,
    bank_statement_id: int,
) -> list[BankStatementLine]:
    await get_bank_statement(
        db,
        company_id=company_id,
        bank_statement_id=bank_statement_id,
    )

    return list(
        (
            await db.execute(
                select(BankStatementLine)
                .where(
                    BankStatementLine.company_id
                    == company_id,
                    BankStatementLine.bank_statement_id
                    == bank_statement_id,
                )
                .order_by(
                    BankStatementLine.transaction_date,
                    BankStatementLine.id,
                )
            )
        ).scalars().all()
    )
