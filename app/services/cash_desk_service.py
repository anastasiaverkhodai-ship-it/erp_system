from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.cash_desk import CashDesk


class CashDeskError(Exception):
    pass


class CashDeskNotFoundError(CashDeskError):
    pass


class CashDeskAccountingAccountError(CashDeskError):
    pass


def normalize_cash_desk_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise CashDeskError("Cash desk name cannot be blank")
    return normalized


def normalize_cash_desk_code(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized:
        raise CashDeskError("Cash desk code cannot be blank")
    return normalized


def normalize_cash_desk_currency_code(value: str) -> str:
    normalized = value.strip().upper()
    if len(normalized) != 3:
        raise CashDeskError(
            "Cash desk currency code must contain exactly 3 characters"
        )
    return normalized


async def _validate_accounting_account(
    db: AsyncSession,
    *,
    company_id: int,
    accounting_account_id: int,
) -> Account:
    account = (
        await db.execute(
            select(Account).where(
                Account.company_id == company_id,
                Account.id == accounting_account_id,
            )
        )
    ).scalar_one_or_none()

    if account is None:
        raise CashDeskAccountingAccountError(
            "Accounting account not found for company"
        )

    if not account.is_active:
        raise CashDeskAccountingAccountError(
            "Accounting account is inactive"
        )

    if not account.is_postable:
        raise CashDeskAccountingAccountError(
            "Accounting account is not postable"
        )

    return account


async def create_cash_desk(
    db: AsyncSession,
    *,
    company_id: int,
    name: str,
    code: str,
    currency_code: str,
    accounting_account_id: int,
) -> CashDesk:
    name = normalize_cash_desk_name(name)
    code = normalize_cash_desk_code(code)
    currency_code = normalize_cash_desk_currency_code(
        currency_code
    )

    await _validate_accounting_account(
        db,
        company_id=company_id,
        accounting_account_id=accounting_account_id,
    )

    cash_desk = CashDesk(
        company_id=company_id,
        name=name,
        code=code,
        currency_code=currency_code,
        accounting_account_id=accounting_account_id,
    )
    db.add(cash_desk)
    await db.flush()

    return cash_desk


async def get_cash_desk(
    db: AsyncSession,
    *,
    company_id: int,
    cash_desk_id: int,
) -> CashDesk:
    cash_desk = (
        await db.execute(
            select(CashDesk).where(
                CashDesk.company_id == company_id,
                CashDesk.id == cash_desk_id,
            )
        )
    ).scalar_one_or_none()

    if cash_desk is None:
        raise CashDeskNotFoundError(
            "Cash desk not found"
        )

    return cash_desk


async def list_cash_desks(
    db: AsyncSession,
    *,
    company_id: int,
) -> tuple[CashDesk, ...]:
    return tuple(
        (
            await db.execute(
                select(CashDesk)
                .where(
                    CashDesk.company_id == company_id
                )
                .order_by(CashDesk.id)
            )
        ).scalars().all()
    )


async def update_cash_desk(
    db: AsyncSession,
    *,
    company_id: int,
    cash_desk_id: int,
    name: str | None = None,
    code: str | None = None,
    currency_code: str | None = None,
    accounting_account_id: int | None = None,
    is_active: bool | None = None,
) -> CashDesk:
    cash_desk = await get_cash_desk(
        db,
        company_id=company_id,
        cash_desk_id=cash_desk_id,
    )

    if name is not None:
        cash_desk.name = normalize_cash_desk_name(name)

    if code is not None:
        cash_desk.code = normalize_cash_desk_code(code)

    if currency_code is not None:
        cash_desk.currency_code = (
            normalize_cash_desk_currency_code(
                currency_code
            )
        )

    if accounting_account_id is not None:
        await _validate_accounting_account(
            db,
            company_id=company_id,
            accounting_account_id=accounting_account_id,
        )
        cash_desk.accounting_account_id = (
            accounting_account_id
        )

    if is_active is not None:
        cash_desk.is_active = is_active

    await db.flush()
    return cash_desk
