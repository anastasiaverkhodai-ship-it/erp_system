from __future__ import annotations

from app.services.payroll_mutation_guard import serialized_payroll_mutation

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.journal_entry import JournalEntry, JournalEntryStatus
from app.models.payroll import PayrollCalculation, PayrollPeriod
from app.models.payroll_deduction_result import PayrollDeductionResult
from app.services.accounting_account_role_resolver import (
    AccountingAccountRoleResolutionError,
    resolve_company_account_roles,
)
from app.services.accounting_account_roles import AccountingAccountRole
from app.services.accounting_reversal import reverse_journal_entry
from app.models.journal_entry_line import JournalEntryLine


class PayrollDeductionAccountingError(Exception):
    pass


class PayrollDeductionAccountingNotFoundError(
    PayrollDeductionAccountingError
):
    pass


class PayrollDeductionAccountingConflictError(
    PayrollDeductionAccountingError
):
    pass


async def get_payroll_deduction_journal(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_deduction_result_id: int,
) -> JournalEntry | None:
    return await db.scalar(
        select(JournalEntry).where(
            JournalEntry.company_id == company_id,
            JournalEntry.payroll_deduction_result_id
            == payroll_deduction_result_id,
            JournalEntry.reversal_of_id.is_(None),
        )
    )


@serialized_payroll_mutation(PayrollDeductionAccountingError)
async def generate_and_post_payroll_deduction_journal(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_deduction_result_id: int,
    created_by: int,
) -> JournalEntry:
    existing = await get_payroll_deduction_journal(
        db,
        company_id=company_id,
        payroll_deduction_result_id=payroll_deduction_result_id,
    )
    if existing is not None:
        if existing.status != JournalEntryStatus.POSTED:
            raise PayrollDeductionAccountingConflictError('Deduction journal is not active; use a correction')
        return existing

    result = await db.scalar(
        select(PayrollDeductionResult).where(
            PayrollDeductionResult.company_id == company_id,
            PayrollDeductionResult.id == payroll_deduction_result_id,
        )
    )

    if result is None:
        raise PayrollDeductionAccountingNotFoundError(
            "Payroll deduction result not found"
        )

    amount = Decimal(result.deduction_amount)

    if amount <= 0:
        raise PayrollDeductionAccountingConflictError(
            "Payroll deduction amount must be positive"
        )

    calculation = await db.scalar(
        select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id == result.payroll_calculation_id,
        )
    )

    if calculation is None:
        raise PayrollDeductionAccountingNotFoundError(
            "Payroll calculation not found"
        )

    from app.services.payroll_revision_service import require_current_calculation
    await require_current_calculation(db, calculation=calculation, error_type=PayrollDeductionAccountingConflictError)

    if (
        calculation.employment_contract_id
        != result.employment_contract_id
    ):
        raise PayrollDeductionAccountingConflictError(
            "Payroll deduction result contract provenance mismatch"
        )

    try:
        accounts = await resolve_company_account_roles(
            db,
            company_id=company_id,
            roles=(
                AccountingAccountRole.PAYROLL_NET_PAYABLE,
                AccountingAccountRole.PAYROLL_DEDUCTION_PAYABLE,
            ),
        )
    except AccountingAccountRoleResolutionError as exc:
        raise PayrollDeductionAccountingConflictError(
            str(exc)
        ) from exc

    net_payable = accounts[
        AccountingAccountRole.PAYROLL_NET_PAYABLE
    ]

    deduction_payable = accounts[
        AccountingAccountRole.PAYROLL_DEDUCTION_PAYABLE
    ]

    period = await db.scalar(select(PayrollPeriod).where(
        PayrollPeriod.company_id == company_id, PayrollPeriod.id == calculation.payroll_period_id))
    if period is None:
        raise PayrollDeductionAccountingNotFoundError('Payroll period not found')

    amount = amount.quantize(Decimal("0.01"))
    now = datetime.utcnow()

    entry = JournalEntry(
        company_id=company_id,
        payroll_deduction_result_id=result.id,
        entry_date=period.end_date,
        description=(
            "Payroll non-statutory deduction liability "
            f"for payroll calculation {calculation.id}"
        ),
        status=JournalEntryStatus.DRAFT,
        created_by=created_by,
        created_at=now,

    )

    entry.lines = [
        JournalEntryLine(
            line_no=1,
            account_id=net_payable.id,
            debit=amount,
            credit=Decimal("0.00"),
            description=(
                "Employee payroll deduction reclassification"
            ),
        ),
        JournalEntryLine(
            line_no=2,
            account_id=deduction_payable.id,
            debit=Decimal("0.00"),
            credit=amount,
            description="Payroll deduction liability",
        ),
    ]

    from app.services.accounting_posting import post_journal_entry, AccountingPostingError
    try:
        async with db.begin_nested():
            db.add(entry)
            await db.flush()
            return await post_journal_entry(db, company_id, entry.id)
    except AccountingPostingError as exc:
        raise PayrollDeductionAccountingConflictError(str(exc)) from exc


async def reverse_payroll_deduction_journal(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_deduction_result_id: int,
    reversal_date: date,
    reversed_by: int,
) -> JournalEntry:
    journal = await get_payroll_deduction_journal(
        db,
        company_id=company_id,
        payroll_deduction_result_id=payroll_deduction_result_id,
    )

    if journal is None:
        raise PayrollDeductionAccountingNotFoundError(
            "Payroll deduction accounting journal not found"
        )

    if journal.reversed_at is not None:
        raise PayrollDeductionAccountingConflictError(
            "Payroll deduction accounting journal is already reversed"
        )

    reversal = await reverse_journal_entry(
        db,
        company_id=company_id,
        journal_entry_id=journal.id,
        reversal_date=reversal_date,
        reversed_by=reversed_by,
    )

    if (
        reversal.payroll_deduction_result_id
        != payroll_deduction_result_id
    ):
        raise PayrollDeductionAccountingConflictError(
            "Payroll deduction reversal provenance mismatch"
        )

    return reversal
