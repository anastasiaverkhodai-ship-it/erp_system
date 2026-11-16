"""Source-bound validation for a consolidated payroll bank line.

This is a read/lock/validate operation, NOT reconciliation posting.
It creates no additional disbursement, journal or bank movement.
"""

from decimal import Decimal
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank_statement import BankStatement
from app.models.bank_statement_line import BankStatementLine
from app.models.bank_statement_reconciliation import BankStatementReconciliationActiveLink
from app.models.journal_entry import JournalEntry
from app.models.payroll_disbursement import PayrollDisbursement


class PayrollBankBatchValidationError(ValueError):
    pass


def _positive_id(value, label):
    if type(value) is not int or value <= 0:
        raise PayrollBankBatchValidationError(
            f"Invalid {label}"
        )


async def validate_payroll_bank_batch(
    db: AsyncSession,
    *,
    company_id: int,
    employee_id: int,
    bank_statement_line_id: int,
    payroll_disbursement_ids,
):
    """Validate a full-payment candidate for one outgoing bank line.

    The caller owns the transaction. Bank line is row-locked to
    serialize checks with existing reconciliation operations.
    """
    _positive_id(company_id, "company")
    _positive_id(employee_id, "employee")
    _positive_id(bank_statement_line_id, "statement line")

    if (
        type(payroll_disbursement_ids) not in (list, tuple)
        or len(payroll_disbursement_ids) < 2
    ):
        raise PayrollBankBatchValidationError(
            "At least two payroll disbursements required"
        )

    ids = list(payroll_disbursement_ids)

    if (
        any(type(x) is not int or x <= 0 for x in ids)
        or len(set(ids)) != len(ids)
    ):
        raise PayrollBankBatchValidationError(
            "Invalid or duplicate disbursement"
        )

    # Existing reconciliation also locks this row.
    line = await db.scalar(
        select(BankStatementLine)
        .where(
            BankStatementLine.company_id == company_id,
            BankStatementLine.id == bank_statement_line_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if line is None:
        raise PayrollBankBatchValidationError(
            "Bank statement line unavailable"
        )

    if Decimal(line.amount) >= 0:
        raise PayrollBankBatchValidationError(
            "Outgoing bank statement line required"
        )

    if line.currency_code != "UAH":
        raise PayrollBankBatchValidationError(
            "UAH bank statement required"
        )

    current_link = await db.scalar(
        select(BankStatementReconciliationActiveLink.id)
        .where(
            BankStatementReconciliationActiveLink.company_id == company_id,
            BankStatementReconciliationActiveLink.bank_statement_line_id
                == bank_statement_line_id,
        )
    )

    if current_link is not None:
        raise PayrollBankBatchValidationError(
            "Bank statement line already reconciled"
        )

    payouts = (
        await db.scalars(
            select(PayrollDisbursement)
            .where(
                PayrollDisbursement.company_id == company_id,
                PayrollDisbursement.id.in_(ids),
            )
            .order_by(PayrollDisbursement.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()

    if len(payouts) != len(ids):
        raise PayrollBankBatchValidationError(
            "One or more payroll disbursements unavailable"
        )

    from app.services.payroll_bank_batch_service import active_batch_for_payout

    total = Decimal("0.00")
    members = []

    for payout in payouts:
        if payout.employee_id != employee_id:
            raise PayrollBankBatchValidationError(
                "Mixed employee batch forbidden"
            )

        if (
            payout.bank_account_id is None
            or payout.cash_desk_id is not None
            or payout.bank_account_id != line.bank_account_id
        ):
            raise PayrollBankBatchValidationError(
                "Bank account or payment source mismatch"
            )

        if (
            payout.currency_code != "UAH"
            or payout.currency_code != line.currency_code
        ):
            raise PayrollBankBatchValidationError(
                "Currency mismatch"
            )

        if (
            payout.cancelled_at is not None
            or payout.reversed_on is not None
            or payout.confirmed_at is None
        ):
            raise PayrollBankBatchValidationError(
                "Only active confirmed payouts permitted"
            )

        posted_id = await db.scalar(
            select(JournalEntry.id)
            .where(
                JournalEntry.company_id == company_id,
                JournalEntry.payroll_disbursement_id == payout.id,
                JournalEntry.reversal_of_id.is_(None),
                JournalEntry.status == "posted",
            )
        )

        if posted_id is None:
            raise PayrollBankBatchValidationError(
                "Payroll disbursement journal not posted"
            )

        existing = await db.scalar(
            select(BankStatementReconciliationActiveLink.id)
            .where(
                BankStatementReconciliationActiveLink.company_id == company_id,
                BankStatementReconciliationActiveLink.payroll_disbursement_id
                    == payout.id,
            )
        )

        if existing is not None or await active_batch_for_payout(db, company_id, payout.id) is not None:
            raise PayrollBankBatchValidationError(
                "Payroll disbursement already reconciled"
            )

        amount = Decimal(payout.amount)

        if not amount.is_finite() or amount <= 0:
            raise PayrollBankBatchValidationError(
                "Invalid payroll amount"
            )

        total += amount
        members.append({
            "payroll_disbursement_id": payout.id,
            "journal_entry_id": posted_id,
            "amount": format(amount, ".2f"),
        })

    if total != -Decimal(line.amount):
        raise PayrollBankBatchValidationError(
            "Bank line total does not equal payout total"
        )

    return {
        "company_id": company_id,
        "employee_id": employee_id,
        "bank_statement_line_id": bank_statement_line_id,
        "bank_account_id": line.bank_account_id,
        "currency_code": "UAH",
        "amount": format(total, ".2f"),
        "members": members,
        "member_count": len(members),
        "mode": "VALIDATION_ONLY",
        "reconciliation_posted": False,
        "new_journals_created": 0,
    }
