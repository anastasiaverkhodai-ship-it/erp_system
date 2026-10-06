from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.bank_account import BankAccount
from app.models.employment_contract import EmploymentContract
from app.models.payroll import PayrollPeriod, PayrollPeriodStatus
from app.models.payroll_advance import PayrollAdvance
from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.accounting_reversal import reverse_journal_entry
from app.services.accounting_posting import post_journal_entry
from app.services.accounting_account_roles import AccountingAccountRole
from app.services.accounting_account_role_resolver import (
    AccountingAccountRoleResolutionError,
    resolve_company_account_roles,
)
from app.models.journal_entry_line import JournalEntryLine
from app.models.journal_entry import JournalEntry, JournalEntryStatus
from app.models.account import Account


MONEY = Decimal("0.01")
ZERO = Decimal("0.00")


class PayrollAdvanceError(Exception):
    pass


class PayrollAdvanceNotFoundError(PayrollAdvanceError):
    pass


class PayrollAdvanceSourceStateError(PayrollAdvanceError):
    pass


class PayrollAdvanceCurrencyError(PayrollAdvanceError):
    pass


def _money(value: Decimal) -> Decimal:
    value = Decimal(value)

    if not value.is_finite():
        raise PayrollAdvanceSourceStateError(
            "Payroll advance amount must be finite"
        )

    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


async def get_payroll_advance(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_period_id: int,
    employment_contract_id: int,
) -> PayrollAdvance | None:
    return (
        await db.execute(
            select(PayrollAdvance).where(
                PayrollAdvance.company_id == company_id,
                PayrollAdvance.payroll_period_id == payroll_period_id,
                PayrollAdvance.employment_contract_id
                == employment_contract_id,
            )
        )
    ).scalar_one_or_none()


async def create_payroll_advance(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_period_id: int,
    employment_contract_id: int,
    bank_account_id: int,
    advance_percentage: Decimal,
    calculation_base_amount: Decimal,
    minimum_due_amount: Decimal,
    currency_code: str,
    payment_date,
    created_by: int,
) -> PayrollAdvance:
    existing = await get_payroll_advance(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        employment_contract_id=employment_contract_id,
    )

    if existing is not None:
        return existing

    period = (
        await db.execute(
            select(PayrollPeriod).where(
                PayrollPeriod.company_id == company_id,
                PayrollPeriod.id == payroll_period_id,
            )
        )
    ).scalar_one_or_none()

    if period is None:
        raise PayrollAdvanceNotFoundError("Payroll period not found")

    if period.status == PayrollPeriodStatus.FINALIZED:
        raise PayrollAdvanceSourceStateError(
            "Payroll advance cannot be created for finalized payroll period"
        )

    contract = (
        await db.execute(
            select(EmploymentContract).where(
                EmploymentContract.company_id == company_id,
                EmploymentContract.id == employment_contract_id,
            )
        )
    ).scalar_one_or_none()

    if contract is None:
        raise PayrollAdvanceNotFoundError(
            "Employment contract not found"
        )

    bank = (
        await db.execute(
            select(BankAccount).where(
                BankAccount.company_id == company_id,
                BankAccount.id == bank_account_id,
            )
        )
    ).scalar_one_or_none()

    if bank is None:
        raise PayrollAdvanceNotFoundError("Bank account not found")

    if not bank.is_active:
        raise PayrollAdvanceSourceStateError(
            "Bank account must be active"
        )

    currency = currency_code.upper()

    if bank.currency_code != currency:
        raise PayrollAdvanceCurrencyError(
            "Payroll advance and bank account currencies must match"
        )

    percentage = Decimal(advance_percentage)

    if not percentage.is_finite() or percentage <= ZERO or percentage > Decimal("100"):
        raise PayrollAdvanceSourceStateError(
            "Advance percentage must be greater than zero and at most 100"
        )

    base = _money(calculation_base_amount)
    minimum = _money(minimum_due_amount)

    if base <= ZERO:
        raise PayrollAdvanceSourceStateError(
            "Advance calculation base must be positive"
        )

    if minimum < ZERO:
        raise PayrollAdvanceSourceStateError(
            "Minimum due amount cannot be negative"
        )

    calculated = _money(
        base * percentage / Decimal("100")
    )

    if calculated < minimum:
        raise PayrollAdvanceSourceStateError(
            "Calculated payroll advance is below minimum due amount"
        )

    advance = PayrollAdvance(
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        employment_contract_id=employment_contract_id,
        bank_account_id=bank_account_id,
        advance_percentage=percentage,
        calculation_base_amount=base,
        calculated_amount=calculated,
        minimum_due_amount=minimum,
        paid_amount=calculated,
        currency_code=currency,
        payment_date=payment_date,
        created_by=created_by,
    )

    db.add(advance)
    await db.flush()

    return advance



async def get_payroll_advance_journal(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_advance_id: int,
) -> JournalEntry | None:
    return (
        await db.execute(
            select(JournalEntry)
            .execution_options(populate_existing=True)
            .options(selectinload(JournalEntry.lines))
            .where(
                JournalEntry.company_id == company_id,
                JournalEntry.payroll_advance_id == payroll_advance_id,
                JournalEntry.reversal_of_id.is_(None),
            )
        )
    ).scalar_one_or_none()


@serialized_payroll_mutation(PayrollAdvanceSourceStateError)
async def generate_and_post_payroll_advance_journal(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_advance_id: int,
    created_by: int,
) -> JournalEntry:
    existing = await get_payroll_advance_journal(
        db,
        company_id=company_id,
        payroll_advance_id=payroll_advance_id,
    )
    if existing is not None:
        if existing.status != JournalEntryStatus.POSTED:
            raise PayrollAdvanceSourceStateError(
                "Payroll advance journal is not active after reversal"
            )
        return existing

    advance = (
        await db.execute(
            select(PayrollAdvance)
            .execution_options(populate_existing=True)
            .where(
                PayrollAdvance.company_id == company_id,
                PayrollAdvance.id == payroll_advance_id,
            )
        )
    ).scalar_one_or_none()

    if advance is None:
        raise PayrollAdvanceNotFoundError("Payroll advance not found")

    bank_account = (
        await db.execute(
            select(BankAccount).where(
                BankAccount.company_id == company_id,
                BankAccount.id == advance.bank_account_id,
            )
        )
    ).scalar_one_or_none()

    if bank_account is None or not bank_account.is_active:
        raise PayrollAdvanceSourceStateError(
            "Active bank account is required"
        )

    if bank_account.currency_code != advance.currency_code:
        raise PayrollAdvanceCurrencyError(
            "Bank account currency does not match payroll advance"
        )

    bank_gl = (
        await db.execute(
            select(Account).where(
                Account.company_id == company_id,
                Account.id == bank_account.accounting_account_id,
            )
        )
    ).scalar_one_or_none()

    if bank_gl is None:
        raise PayrollAdvanceSourceStateError(
            "Bank accounting account not found"
        )

    if not bank_gl.is_active or not bank_gl.is_postable:
        raise PayrollAdvanceSourceStateError(
            "Bank accounting account must be active and postable"
        )

    try:
        accounts = await resolve_company_account_roles(
            db,
            company_id=company_id,
            roles=(AccountingAccountRole.PAYROLL_NET_PAYABLE,),
        )
    except AccountingAccountRoleResolutionError as exc:
        raise PayrollAdvanceError(str(exc)) from exc

    payable = accounts[AccountingAccountRole.PAYROLL_NET_PAYABLE]
    amount = Decimal(advance.paid_amount)

    if amount <= Decimal("0.00"):
        raise PayrollAdvanceSourceStateError(
            "Payroll advance paid amount must be positive"
        )

    journal = JournalEntry(
        company_id=company_id,
        payroll_advance_id=advance.id,
        entry_date=advance.payment_date,
        description=f"Payroll advance {advance.id}",
        status=JournalEntryStatus.DRAFT,
        created_by=created_by,
    )

    journal.lines = [
        JournalEntryLine(
            line_no=1,
            account_id=payable.id,
            debit=amount,
            credit=Decimal("0.00"),
            description="Payroll net payable advance settlement",
        ),
        JournalEntryLine(
            line_no=2,
            account_id=bank_gl.id,
            debit=Decimal("0.00"),
            credit=amount,
            description="Payroll advance bank payment",
        ),
    ]

    db.add(journal)
    await db.flush()

    return await post_journal_entry(
        db,
        company_id,
        journal.id,
    )


@serialized_payroll_mutation(PayrollAdvanceSourceStateError)
async def reverse_payroll_advance_journal(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_advance_id: int,
    reversal_date: date,
    reversed_by: int,
) -> JournalEntry:
    original = await get_payroll_advance_journal(
        db,
        company_id=company_id,
        payroll_advance_id=payroll_advance_id,
    )

    if original is None:
        raise PayrollAdvanceNotFoundError(
            "Payroll advance journal not found"
        )

    return await reverse_journal_entry(
        db,
        company_id,
        original.id,
        reversal_date,
        reversed_by,
    )
