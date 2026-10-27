"""Build the signed accrual difference; never reverse an actual cash movement."""
from decimal import Decimal
from app.models.journal_entry import JournalEntry, JournalEntryStatus
from app.models.journal_entry_line import JournalEntryLine
from app.services.accounting_account_roles import AccountingAccountRole as Role
from app.services.accounting_account_role_resolver import resolve_company_account_roles
from app.services.accounting_period_service import ensure_period_open
from app.services.payroll_sick_correction_plan import plan_sick_correction

ROLES = {
    'payroll_expense': Role.PAYROLL_SICK_EXPENSE,
    'insurance_receivable': Role.PAYROLL_INSURANCE_RECEIVABLE,
    'employer_contribution_expense': Role.PAYROLL_EMPLOYER_CONTRIBUTION_EXPENSE,
    'payroll_net_payable': Role.PAYROLL_NET_PAYABLE,
    'personal_income_tax_payable': Role.PAYROLL_PIT_PAYABLE,
    'military_levy_payable': Role.PAYROLL_MILITARY_LEVY_PAYABLE,
    'employer_contribution_payable': Role.PAYROLL_USC_PAYABLE,
}


async def build_sick_difference_journal(db, *, company_id, replacement_calculation_id,
                                      original, replacement, correction_date, actor_id):
    await ensure_period_open(company_id, correction_date, db)
    # Settlement values do not participate in accrual differences. Actual
    # employee/PFU balances are loaded separately from confirmed documents.
    plan = plan_sick_correction(original=original, replacement=replacement,
        employee_settled=Decimal('0.00'), insurer_received=Decimal('0.00'))
    sides = plan.journal_sides()
    if not sides:
        return None
    roles = tuple(dict.fromkeys(ROLES[name] for name, _, _ in sides))
    accounts = await resolve_company_account_roles(db, company_id=company_id, roles=roles)
    journal = JournalEntry(company_id=company_id, payroll_calculation_id=replacement_calculation_id,
        entry_date=correction_date, status=JournalEntryStatus.DRAFT, created_by=actor_id,
        description=f'Sick-pay correction difference for payroll {replacement_calculation_id}')
    journal.lines = [JournalEntryLine(line_no=index, account_id=accounts[ROLES[name]].id,
        debit=debit, credit=credit, description='Sick-pay correction: ' + name)
        for index, (name, debit, credit) in enumerate(sides, 1)]
    db.add(journal)
    await db.flush()
    return journal
