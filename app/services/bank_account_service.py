from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.bank_account import BankAccount
from app.models.company import Company


class BankAccountError(Exception):
    pass


class BankAccountCompanyNotFoundError(
    BankAccountError
):
    pass


class BankAccountAccountingAccountError(
    BankAccountError
):
    pass


class BankAccountNotFoundError(
    BankAccountError
):
    pass


class BankAccountDuplicateError(
    BankAccountError
):
    pass


def normalize_bank_account_name(
    value: str,
) -> str:
    normalized = value.strip()

    if not normalized:
        raise BankAccountError(
            "Bank account name cannot be blank"
        )

    return normalized


def normalize_bank_account_number(
    value: str,
) -> str:
    normalized = value.strip().upper()

    if not normalized:
        raise BankAccountError(
            "Bank account number cannot be blank"
        )

    return normalized


def normalize_bank_account_currency_code(
    value: str,
) -> str:
    normalized = value.strip().upper()

    if len(normalized) != 3:
        raise BankAccountError(
            "Bank account currency code "
            "must contain exactly 3 characters"
        )

    return normalized


async def _require_active_company(
    db: AsyncSession,
    *,
    company_id: int,
) -> Company:
    company = (
        await db.execute(
            select(Company).where(
                Company.id == company_id,
                Company.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()

    if company is None:
        raise BankAccountCompanyNotFoundError(
            "Active company not found"
        )

    return company


async def _require_valid_accounting_account(
    db: AsyncSession,
    *,
    company_id: int,
    accounting_account_id: int,
) -> Account:
    account = (
        await db.execute(
            select(Account).where(
                Account.id == accounting_account_id,
                Account.company_id == company_id,
            )
        )
    ).scalar_one_or_none()

    if account is None:
        raise BankAccountAccountingAccountError(
            "Accounting account not found "
            "for this company"
        )

    if not account.is_active:
        raise BankAccountAccountingAccountError(
            "Accounting account is inactive"
        )

    if not account.is_postable:
        raise BankAccountAccountingAccountError(
            "Accounting account is not postable"
        )

    return account


async def _assert_number_available(
    db: AsyncSession,
    *,
    company_id: int,
    account_number: str,
    exclude_bank_account_id: int | None = None,
) -> None:
    query = select(
        BankAccount.id
    ).where(
        BankAccount.company_id == company_id,
        BankAccount.account_number
        == account_number,
    )

    if exclude_bank_account_id is not None:
        query = query.where(
            BankAccount.id
            != exclude_bank_account_id
        )

    existing = (
        await db.execute(query)
    ).scalar_one_or_none()

    if existing is not None:
        raise BankAccountDuplicateError(
            "Bank account number already "
            "exists in this company"
        )


async def list_bank_accounts(
    db: AsyncSession,
    *,
    company_id: int,
) -> list[BankAccount]:
    return list(
        (
            await db.execute(
                select(BankAccount)
                .where(
                    BankAccount.company_id
                    == company_id
                )
                .order_by(
                    BankAccount.name.asc(),
                    BankAccount.id.asc(),
                )
            )
        ).scalars().all()
    )


async def get_bank_account(
    db: AsyncSession,
    *,
    company_id: int,
    bank_account_id: int,
    lock_row: bool = False,
) -> BankAccount:
    query = select(
        BankAccount
    ).where(
        BankAccount.id == bank_account_id,
        BankAccount.company_id == company_id,
    )

    if lock_row:
        query = query.with_for_update()

    bank_account = (
        await db.execute(query)
    ).scalar_one_or_none()

    if bank_account is None:
        raise BankAccountNotFoundError(
            "Bank account not found"
        )

    return bank_account


async def create_bank_account(
    db: AsyncSession,
    *,
    company_id: int,
    name: str,
    account_number: str,
    currency_code: str,
    accounting_account_id: int,
) -> BankAccount:
    """
    Create BankAccount master data.

    Caller owns COMMIT / ROLLBACK.
    """

    await _require_active_company(
        db,
        company_id=company_id,
    )

    await _require_valid_accounting_account(
        db,
        company_id=company_id,
        accounting_account_id=(
            accounting_account_id
        ),
    )

    normalized_number = (
        normalize_bank_account_number(
            account_number
        )
    )

    await _assert_number_available(
        db,
        company_id=company_id,
        account_number=normalized_number,
    )

    bank_account = BankAccount(
        company_id=company_id,
        name=normalize_bank_account_name(
            name
        ),
        account_number=normalized_number,
        currency_code=(
            normalize_bank_account_currency_code(
                currency_code
            )
        ),
        accounting_account_id=(
            accounting_account_id
        ),
        is_active=True,
    )

    db.add(bank_account)
    await db.flush()

    return bank_account


async def update_bank_account(
    db: AsyncSession,
    *,
    company_id: int,
    bank_account_id: int,
    name: str | None = None,
    account_number: str | None = None,
    currency_code: str | None = None,
    accounting_account_id: int | None = None,
    is_active: bool | None = None,
) -> BankAccount:
    """
    Update current BankAccount master data.

    This master-data lifecycle is mutable.
    Historical BankStatement evidence will be immutable.

    Caller owns COMMIT / ROLLBACK.
    """

    bank_account = await get_bank_account(
        db,
        company_id=company_id,
        bank_account_id=bank_account_id,
        lock_row=True,
    )

    if accounting_account_id is not None:
        await _require_valid_accounting_account(
            db,
            company_id=company_id,
            accounting_account_id=(
                accounting_account_id
            ),
        )

        bank_account.accounting_account_id = (
            accounting_account_id
        )

    if account_number is not None:
        normalized_number = (
            normalize_bank_account_number(
                account_number
            )
        )

        await _assert_number_available(
            db,
            company_id=company_id,
            account_number=normalized_number,
            exclude_bank_account_id=(
                bank_account.id
            ),
        )

        bank_account.account_number = (
            normalized_number
        )

    if name is not None:
        bank_account.name = (
            normalize_bank_account_name(
                name
            )
        )

    if currency_code is not None:
        bank_account.currency_code = (
            normalize_bank_account_currency_code(
                currency_code
            )
        )

    if is_active is not None:
        bank_account.is_active = is_active

    await db.flush()

    return bank_account
