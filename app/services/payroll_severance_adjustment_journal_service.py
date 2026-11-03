"""Build a source-bound severance adjustment GL journal.

Creates real DRAFT journal entries using configured company accounts.
Does not post, approve tax treatment, transfer cash or refund taxes.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models.company import Company
from app.models.journal_entry import JournalEntry
from app.models.payroll_severance_adjustment_journal import (
    PayrollSeveranceAdjustmentJournal,
)
from app.services.payroll_severance_financial_delta_ledger import (
    load_severance_financial_deltas,
)


class SeveranceAdjustmentJournalError(ValueError):
    pass


ZERO = Decimal("0.00")


def correction_journal_sides(delta):
    """Return signed movements as positive debit/credit amounts."""
    if not delta.reconciled:
        raise SeveranceAdjustmentJournalError(
            "Unreconciled severance correction"
        )

    components = (
        ("gross_expense", delta.gross_delta),
        (
            "usc_expense",
            delta.employer_contribution_delta,
        ),
        ("net_payable", -delta.net_delta),
        (
            "pit_payable",
            -delta.personal_income_tax_delta,
        ),
        (
            "military_payable",
            -delta.military_levy_delta,
        ),
        (
            "usc_payable",
            -delta.employer_contribution_delta,
        ),
    )

    sides = tuple(
        (
            role,
            max(value, ZERO),
            max(-value, ZERO),
        )
        for role, value in components
        if value != ZERO
    )

    debit = sum(
        (row[1] for row in sides), ZERO
    )
    credit = sum(
        (row[2] for row in sides), ZERO
    )

    if not sides or debit != credit:
        raise SeveranceAdjustmentJournalError(
            "Unbalanced severance correction journal"
        )

    return sides


async def create_severance_adjustment_draft(
    db,
    *,
    company_id,
    correction_case_id,
    correction_date,
    actor_id,
):
    if (
        type(company_id) is not int
        or company_id <= 0
        or type(correction_case_id) is not int
        or correction_case_id <= 0
        or type(actor_id) is not int
        or actor_id <= 0
        or type(correction_date) is not date
    ):
        raise SeveranceAdjustmentJournalError(
            "Valid company, case, actor and correction date required"
        )

    from app.models.payroll_severance_correction_case import (
        PayrollSeveranceCorrectionCase,
    )
    from app.models.payroll_severance_financial_proposal import (
        PayrollSeveranceFinancialProposal,
    )
    from app.services.payroll_accounting_service import (
        _payroll_roles,
        resolve_company_account_roles,
    )
    from app.models.journal_entry_line import JournalEntryLine
    from app.models.journal_entry import JournalEntryStatus
    from app.services.accounting_period_service import (
        ensure_period_open,
    )

    company = await db.scalar(
        select(Company.id)
        .where(Company.id == company_id)
        .with_for_update()
    )

    if company is None:
        raise SeveranceAdjustmentJournalError(
            "Company not found"
        )

    case = await db.scalar(
        select(PayrollSeveranceCorrectionCase).where(
            PayrollSeveranceCorrectionCase.company_id == company_id,
            PayrollSeveranceCorrectionCase.id
            == correction_case_id,
        )
    )

    if case is None or case.status != "review_required":
        raise SeveranceAdjustmentJournalError(
            "Active correction case required"
        )

    proposal = await db.scalar(
        select(PayrollSeveranceFinancialProposal).where(
            PayrollSeveranceFinancialProposal.company_id
            == company_id,
            PayrollSeveranceFinancialProposal.correction_case_id
            == correction_case_id,
        )
    )

    if proposal is None or proposal.status != "review_required":
        raise SeveranceAdjustmentJournalError(
            "Immutable review-stage proposal required"
        )

    existing = await db.scalar(
        select(PayrollSeveranceAdjustmentJournal).where(
            PayrollSeveranceAdjustmentJournal.company_id
            == company_id,
            PayrollSeveranceAdjustmentJournal.financial_proposal_id
            == proposal.id,
        )
    )

    if existing is not None:
        if existing.created_by != actor_id:
            raise SeveranceAdjustmentJournalError(
                "Existing journal belongs to another request actor"
            )
        journal = await db.scalar(
            select(JournalEntry).where(
                JournalEntry.company_id == company_id,
                JournalEntry.id == existing.journal_entry_id,
            )
        )
        if journal is None or journal.entry_date != correction_date:
            raise SeveranceAdjustmentJournalError(
                "Existing journal source/date mismatch"
            )
        return journal

    if (
        correction_date < case.as_of_date
        or correction_date < proposal.document_date
    ):
        raise SeveranceAdjustmentJournalError(
            "Correction date precedes financial evidence"
        )

    delta = await load_severance_financial_deltas(
        db,
        company_id=company_id,
        correction_case_id=correction_case_id,
    )

    sides = correction_journal_sides(delta)

    await ensure_period_open(
        company_id=company_id,
        operation_date=correction_date,
        db=db,
    )

    role_map = _payroll_roles()
    required = tuple(
        dict.fromkeys(
            role_map[name]
            for name, _, _ in sides
        )
    )

    accounts = await resolve_company_account_roles(
        db,
        company_id=company_id,
        roles=required,
    )

    # No payroll_calculation_id on this distinct GL adjustment:
    # the original payroll accrual remains untouched and its unique
    # journal source must not be reused.
    journal = JournalEntry(
        company_id=company_id,
        entry_date=correction_date,
        status=JournalEntryStatus.DRAFT,
        created_by=actor_id,
        description=(
            "Unposted severance correction proposal "
            f"{proposal.id}, payroll {case.original_payroll_calculation_id}"
        ),
    )

    journal.lines = [
        JournalEntryLine(
            line_no=index,
            account_id=accounts[role_map[name]].id,
            debit=debit,
            credit=credit,
            description=f"Severance adjustment: {name}",
        )
        for index, (name, debit, credit)
        in enumerate(sides, 1)
    ]

    db.add(journal)
    await db.flush()

    link = PayrollSeveranceAdjustmentJournal(
        company_id=company_id,
        financial_proposal_id=proposal.id,
        journal_entry_id=journal.id,
        created_by=actor_id,
    )

    db.add(link)
    await db.flush()

    return journal
