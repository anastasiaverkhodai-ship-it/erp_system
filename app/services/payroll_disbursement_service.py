from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.services.payroll_mutation_guard import serialized_payroll_mutation

from sqlalchemy import select, func, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.account import Account
from app.models.bank_account import BankAccount
from app.models.employee import Employee
from app.models.employment_contract import EmploymentContract
from app.models.journal_entry import JournalEntry, JournalEntryStatus
from app.models.journal_entry_line import JournalEntryLine
from app.models.payroll import PayrollCalculation, PayrollPeriod
from app.models.payroll_disbursement import PayrollDisbursement
from app.models.payroll_statutory import PayrollStatutoryResult
from app.services.accounting_account_role_resolver import (
    AccountingAccountRoleResolutionError,
    resolve_company_account_roles,
)
from app.services.accounting_account_roles import AccountingAccountRole
from app.services.accounting_posting import post_journal_entry
from app.services.accounting_reversal import reverse_journal_entry


class PayrollDisbursementError(Exception):
    pass


class PayrollDisbursementNotFoundError(PayrollDisbursementError):
    pass


class PayrollDisbursementSourceStateError(PayrollDisbursementError):
    pass


class PayrollDisbursementCurrencyError(PayrollDisbursementError):
    pass


async def get_payroll_disbursement(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_calculation_id: int,
    request_key: str | None = None,
) -> PayrollDisbursement | None:
    return (
        await db.execute(
            select(PayrollDisbursement).execution_options(populate_existing=True).where(
                PayrollDisbursement.request_key == (request_key or f"payroll:{payroll_calculation_id}"),
                PayrollDisbursement.company_id == company_id,
                PayrollDisbursement.payroll_calculation_id
                == payroll_calculation_id,
            )
        )
    ).scalar_one_or_none()


@serialized_payroll_mutation(PayrollDisbursementSourceStateError)
async def create_payroll_disbursement(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_calculation_id: int,
    bank_account_id: int,
    payment_date: date,
    created_by: int,
    amount: Decimal | None = None,
    request_key: str | None = None,
) -> PayrollDisbursement:
    if amount is not None and request_key is None:
        raise PayrollDisbursementSourceStateError('Partial disbursement requires request_key')
    key = request_key.strip() if request_key is not None else f"payroll:{payroll_calculation_id}"
    if not key or len(key) > 200:
        raise PayrollDisbursementSourceStateError('Invalid disbursement request key')
    existing = await get_payroll_disbursement(
        db,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
        request_key=key,
    )
    if existing is not None:
        if existing.cancelled_at is not None:
            raise PayrollDisbursementSourceStateError('Disbursement cancelled; use a new request key')
        requested_amount = amount
        if requested_amount is None:
            requested_amount = await db.scalar(select(PayrollStatutoryResult.net_amount).where(
                PayrollStatutoryResult.company_id == company_id,
                PayrollStatutoryResult.payroll_calculation_id == payroll_calculation_id))
        if (existing.bank_account_id != bank_account_id or existing.payment_date != payment_date
            or (requested_amount is None or Decimal(existing.amount) != requested_amount)):
            raise PayrollDisbursementSourceStateError(
                'Existing disbursement has different bank account or payment date')
        return existing

    calculation = (
        await db.execute(
            select(PayrollCalculation).where(
                PayrollCalculation.company_id == company_id,
                PayrollCalculation.id == payroll_calculation_id,
            )
        )
    ).scalar_one_or_none()
    if calculation is None:
        raise PayrollDisbursementNotFoundError(
            "Payroll calculation not found"
        )

    statutory = (
        await db.execute(
            select(PayrollStatutoryResult).where(
                PayrollStatutoryResult.company_id == company_id,
                PayrollStatutoryResult.payroll_calculation_id
                == calculation.id,
            )
        )
    ).scalar_one_or_none()
    if statutory is None:
        raise PayrollDisbursementSourceStateError(
            "Payroll statutory result is required before disbursement"
        )

    contract = (
        await db.execute(
            select(EmploymentContract).where(
                EmploymentContract.company_id == company_id,
                EmploymentContract.id
                == calculation.employment_contract_id,
            )
        )
    ).scalar_one_or_none()
    if contract is None:
        raise PayrollDisbursementSourceStateError(
            "Employment contract not found"
        )

    employee = (
        await db.execute(
            select(Employee).where(
                Employee.company_id == company_id,
                Employee.id == contract.employee_id,
            )
        )
    ).scalar_one_or_none()
    if employee is None:
        raise PayrollDisbursementSourceStateError(
            "Employee not found"
        )

    iban = getattr(employee, "payment_iban", None)
    if not iban:
        raise PayrollDisbursementSourceStateError(
            "Employee payment IBAN is required"
        )

    bank_account = (
        await db.execute(
            select(BankAccount).where(
                BankAccount.company_id == company_id,
                BankAccount.id == bank_account_id,
            )
        )
    ).scalar_one_or_none()
    if bank_account is None:
        raise PayrollDisbursementNotFoundError(
            "Bank account not found"
        )
    if not bank_account.is_active:
        raise PayrollDisbursementSourceStateError(
            "Bank account must be active"
        )
    if (
        bank_account.currency_code
        != calculation.currency_code
        or statutory.currency_code
        != calculation.currency_code
    ):
        raise PayrollDisbursementCurrencyError(
            "Payroll and bank account currencies must match"
        )

    amount = Decimal(statutory.net_amount) if amount is None else Decimal(amount)
    if not amount.is_finite() or amount != amount.quantize(Decimal('0.01')):
        raise PayrollDisbursementSourceStateError('Disbursement amount must be finite money')
    if amount <= Decimal("0.00"):
        raise PayrollDisbursementSourceStateError(
            "Payroll net amount must be positive"
        )

    reserved = await db.scalar(select(func.coalesce(func.sum(PayrollDisbursement.amount), 0))
        .outerjoin(JournalEntry, and_(
            JournalEntry.company_id == PayrollDisbursement.company_id,
            JournalEntry.payroll_disbursement_id == PayrollDisbursement.id,
            JournalEntry.reversal_of_id.is_(None)))
        .where(PayrollDisbursement.company_id == company_id,
            PayrollDisbursement.payroll_calculation_id == calculation.id,
            PayrollDisbursement.cancelled_at.is_(None),
            or_(JournalEntry.id.is_(None), JournalEntry.status != JournalEntryStatus.REVERSED)))
    if Decimal(reserved) + amount > Decimal(statutory.net_amount):
        raise PayrollDisbursementSourceStateError('Disbursement exceeds remaining net payroll')
    key_owner = await db.scalar(select(PayrollDisbursement.id).where(
        PayrollDisbursement.company_id == company_id, PayrollDisbursement.request_key == key))
    if key_owner is not None:
        raise PayrollDisbursementSourceStateError('Request key belongs to another payroll calculation')

    row = PayrollDisbursement(
        company_id=company_id,
        request_key=key,
        payroll_calculation_id=calculation.id,
        employee_id=employee.id,
        bank_account_id=bank_account.id,
        employee_iban_snapshot=iban,
        amount=amount,
        currency_code=calculation.currency_code,
        payment_date=payment_date,
        created_by=created_by,
    )
    db.add(row)
    await db.flush()
    return row


async def get_payroll_disbursement_journal(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_disbursement_id: int,
) -> JournalEntry | None:
    return (
        await db.execute(
            select(JournalEntry).execution_options(populate_existing=True)
            .options(selectinload(JournalEntry.lines))
            .where(
                JournalEntry.company_id == company_id,
                JournalEntry.payroll_disbursement_id
                == payroll_disbursement_id,
                JournalEntry.reversal_of_id.is_(None),
            )
        )
    ).scalar_one_or_none()


@serialized_payroll_mutation(PayrollDisbursementSourceStateError)
async def generate_and_post_payroll_disbursement_journal(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_disbursement_id: int,
    created_by: int,
) -> JournalEntry:
    existing = await get_payroll_disbursement_journal(
        db,
        company_id=company_id,
        payroll_disbursement_id=payroll_disbursement_id,
    )
    if existing is not None:
        if existing.status != JournalEntryStatus.POSTED:
            raise PayrollDisbursementSourceStateError('Disbursement is not active; use a new request key after reversal')
        return existing

    disbursement = (
        await db.execute(
            select(PayrollDisbursement).execution_options(populate_existing=True).where(
                PayrollDisbursement.company_id == company_id,
                PayrollDisbursement.id == payroll_disbursement_id,
            )
        )
    ).scalar_one_or_none()
    if disbursement is None:
        raise PayrollDisbursementNotFoundError(
            "Payroll disbursement not found"
        )

    bank_account = (
        await db.execute(
            select(BankAccount).where(
                BankAccount.company_id == company_id,
                BankAccount.id == disbursement.bank_account_id,
            )
        )
    ).scalar_one_or_none()
    if bank_account is None or not bank_account.is_active:
        raise PayrollDisbursementSourceStateError(
            "Active bank account is required"
        )

    if bank_account.currency_code != disbursement.currency_code:
        raise PayrollDisbursementCurrencyError(
            "Bank account currency does not match disbursement"
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
        raise PayrollDisbursementSourceStateError(
            "Bank accounting account not found"
        )
    if not bank_gl.is_active or not bank_gl.is_postable:
        raise PayrollDisbursementSourceStateError(
            "Bank accounting account must be active and postable"
        )

    try:
        accounts = await resolve_company_account_roles(
            db,
            company_id=company_id,
            roles=(AccountingAccountRole.PAYROLL_NET_PAYABLE,),
        )
    except AccountingAccountRoleResolutionError as exc:
        raise PayrollDisbursementError(str(exc)) from exc

    payable = accounts[
        AccountingAccountRole.PAYROLL_NET_PAYABLE
    ]
    amount = Decimal(disbursement.amount)

    journal = JournalEntry(
        company_id=company_id,
        payroll_disbursement_id=disbursement.id,
        entry_date=disbursement.payment_date,
        description=(
            "Payroll disbursement "
            f"{disbursement.id}"
        ),
        status=JournalEntryStatus.DRAFT,
        created_by=created_by,
    )
    journal.lines = [
        JournalEntryLine(
            line_no=1,
            account_id=payable.id,
            debit=amount,
            credit=Decimal("0.00"),
            description="Payroll net payable settlement",
        ),
        JournalEntryLine(
            line_no=2,
            account_id=bank_gl.id,
            debit=Decimal("0.00"),
            credit=amount,
            description="Payroll bank disbursement",
        ),
    ]

    db.add(journal)
    await db.flush()

    return await post_journal_entry(
        db,
        company_id,
        journal.id,
    )


@serialized_payroll_mutation(PayrollDisbursementSourceStateError)
async def reverse_payroll_disbursement_journal(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_disbursement_id: int,
    reversal_date: date,
    reversed_by: int,
) -> JournalEntry:
    original = await get_payroll_disbursement_journal(
        db,
        company_id=company_id,
        payroll_disbursement_id=payroll_disbursement_id,
    )
    if original is None:
        raise PayrollDisbursementNotFoundError(
            "Payroll disbursement journal not found"
        )

    return await reverse_journal_entry(
        db,
        company_id,
        original.id,
        reversal_date,
        reversed_by,
    )


async def list_payroll_disbursements(db, *, company_id, payroll_calculation_id):
    return list((await db.scalars(select(PayrollDisbursement).execution_options(populate_existing=True).where(
        PayrollDisbursement.company_id == company_id,
        PayrollDisbursement.payroll_calculation_id == payroll_calculation_id,
    ).order_by(PayrollDisbursement.id))).all())


@serialized_payroll_mutation(PayrollDisbursementSourceStateError)
async def cancel_payroll_disbursement(db, *, company_id, payroll_disbursement_id, cancelled_by):
    row = await db.scalar(select(PayrollDisbursement).where(
        PayrollDisbursement.company_id == company_id,
        PayrollDisbursement.id == payroll_disbursement_id,
    ).execution_options(populate_existing=True))
    if row is None:
        raise PayrollDisbursementNotFoundError('Payroll disbursement not found')
    if row.cancelled_at is not None:
        return row
    journal = await get_payroll_disbursement_journal(db, company_id=company_id,
        payroll_disbursement_id=payroll_disbursement_id)
    if journal is not None and journal.status != JournalEntryStatus.REVERSED:
        raise PayrollDisbursementSourceStateError('Reverse or remove the disbursement journal before cancelling')
    row.cancelled_at = datetime.now(timezone.utc)
    row.cancelled_by = cancelled_by
    await db.flush()
    return row
