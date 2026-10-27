"""Read-only reconciliation against real payroll and tax-remittance rows.

No bank payment, tax remittance, journal or clearing entry is created.
This service does not authorize financial execution.
"""

from dataclasses import dataclass

@dataclass(frozen=True)
class CombinedTaxSettlement:
    monthly_liability: dict
    advance_remitted: dict
    monthly_remitted: dict
    total_remitted: dict
    unpaid: dict


from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models.payroll import PayrollCalculation
from app.models.payroll_advance import PayrollAdvance
from app.models.payroll_statutory import (
    PayrollStatutoryResult,
    PayrollStatutoryResultLine,
)
from app.models.journal_entry import JournalEntry
from app.services.payroll_advance_tax_source_proof import (
    verify_automatic_advance_tax_source_in_db,
)
from app.services.payroll_advance_monthly_tax_clearing import (
    MonthlyTaxClearingError,
    reconcile_monthly_advance_taxes,
)
from app.services.payroll_tax_remittance_service import (
    remittance_commitment,
)

EMPLOYEE_COMPONENTS = (
    "personal_income_tax",
    "military_levy",
)


async def reconcile_monthly_advance_taxes_from_db(
    db,
    *,
    company_id: int,
    payroll_advance_id: int,
    payroll_calculation_id: int,
    as_of: date,
):
    """Read real recorded sources under one caller-owned DB transaction."""
    for name, value in (
        ("company_id", company_id),
        ("payroll_advance_id", payroll_advance_id),
        ("payroll_calculation_id", payroll_calculation_id),
    ):
        if type(value) is not int or value <= 0:
            raise MonthlyTaxClearingError(f"Invalid {name}")

    if type(as_of) is not date:
        raise MonthlyTaxClearingError("Valid as-of date required")

    advance = await db.scalar(
        select(PayrollAdvance).where(
            PayrollAdvance.company_id == company_id,
            PayrollAdvance.id == payroll_advance_id,
        )
    )

    calculation = await db.scalar(
        select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id == payroll_calculation_id,
        )
    )

    if advance is None or calculation is None:
        raise MonthlyTaxClearingError(
            "Company-scoped payroll sources not found"
        )

    if (
        advance.payroll_period_id != calculation.payroll_period_id
        or advance.employment_contract_id
        != calculation.employment_contract_id
    ):
        raise MonthlyTaxClearingError(
            "Advance and calculation belong to different sources"
        )

    if as_of < advance.payment_date:
        raise MonthlyTaxClearingError(
            "Cannot clear before advance payment date"
        )

    proof = await verify_automatic_advance_tax_source_in_db(
        db, advance
    )

    if proof.company_id != company_id:
        raise MonthlyTaxClearingError(
            "Advance proof company mismatch"
        )

    # An advance snapshot is not proof that money left the bank.
    original_advance_journal = await db.scalar(
        select(JournalEntry).where(
            JournalEntry.company_id == company_id,
            JournalEntry.payroll_advance_id == advance.id,
            JournalEntry.reversal_of_id.is_(None),
        )
    )

    if (
        original_advance_journal is None
        or original_advance_journal.entry_date > as_of
    ):
        raise MonthlyTaxClearingError(
            "Advance lacks dated original bank posting"
        )

    advance_reversal = await db.scalar(
        select(JournalEntry.id).where(
            JournalEntry.company_id == company_id,
            JournalEntry.reversal_of_id
            == original_advance_journal.id,
            JournalEntry.status == "posted",
            JournalEntry.entry_date <= as_of,
        ).limit(1)
    )

    if (
        original_advance_journal.status not in ("posted", "reversed")
        or advance_reversal is not None
    ):
        raise MonthlyTaxClearingError(
            "Advance payment is not active as of requested date"
        )

    result = await db.scalar(
        select(PayrollStatutoryResult).where(
            PayrollStatutoryResult.company_id == company_id,
            PayrollStatutoryResult.payroll_calculation_id
            == calculation.id,
        )
    )

    if result is None or result.currency_code != "UAH":
        raise MonthlyTaxClearingError(
            "Real monthly UAH statutory result required"
        )

    accrual = await db.scalar(
        select(JournalEntry).where(
            JournalEntry.company_id == company_id,
            JournalEntry.payroll_calculation_id == calculation.id,
            JournalEntry.reversal_of_id.is_(None),
            JournalEntry.status.in_(("posted", "reversed")),
        )
    )

    if accrual is None or accrual.entry_date > as_of:
        raise MonthlyTaxClearingError(
            "Posted monthly payroll accrual required"
        )

    accrual_reversal = await db.scalar(
        select(JournalEntry.id).where(
            JournalEntry.company_id == company_id,
            JournalEntry.reversal_of_id == accrual.id,
            JournalEntry.status == "posted",
            JournalEntry.entry_date <= as_of,
        ).limit(1)
    )

    if accrual_reversal is not None or accrual.status != "posted":
        raise MonthlyTaxClearingError(
            "Monthly payroll accrual has been reversed"
        )

    statutory_lines = list((
        await db.scalars(
            select(PayrollStatutoryResultLine).where(
                PayrollStatutoryResultLine.company_id
                == company_id,
                PayrollStatutoryResultLine.payroll_statutory_result_id
                == result.id,
            )
        )
    ).all())

    # DATED_CLEARING_AGGREGATION_V1
    # Keep multi-row results fail-closed until the payroll
    # writer proves dated employee-wide allocation and
    # verifies exact statutory rate/source provenance.
    from app.services.payroll_dated_monthly_tax_aggregation import (
        aggregate_dated_monthly_employee_taxes,
        DatedMonthlyTaxAggregationError,
    )

    seen_components = set()
    for line in statutory_lines:
        if line.component not in EMPLOYEE_COMPONENTS:
            continue
        if line.component in seen_components:
            raise MonthlyTaxClearingError(
                "Duplicate monthly statutory component"
            )
        seen_components.add(line.component)

    try:
        monthly = aggregate_dated_monthly_employee_taxes(
            statutory_lines
        )
    except DatedMonthlyTaxAggregationError as exc:
        raise MonthlyTaxClearingError(str(exc)) from exc

    if set(monthly) != set(EMPLOYEE_COMPONENTS):
        raise MonthlyTaxClearingError(
            "Both monthly employee taxes are required"
        )

    advance_components = {}
    for line in proof.components:
        if line.component in EMPLOYEE_COMPONENTS:
            advance_components[line.component] = line.amount

    if set(advance_components) != set(EMPLOYEE_COMPONENTS):
        raise MonthlyTaxClearingError(
            "Both advance employee taxes are required"
        )

    actual_remittances = {}
    for component in EMPLOYEE_COMPONENTS:
        amount = await remittance_commitment(
            db,
            company_id=company_id,
            calculation_id=calculation.id,
            component=component,
            from_date=as_of,
        )
        actual_remittances[component] = amount

    cleared = reconcile_monthly_advance_taxes(
        monthly_gross=result.gross_amount,
        monthly_net=result.net_amount,
        advance_gross=proof.gross_amount,
        advance_net=proof.net_amount,
        advance_tax_calculated=advance_components,
        monthly_tax_liability=monthly,
        tax_actually_remitted=actual_remittances,
    )

    if cleared.financial_execution_allowed:
        raise MonthlyTaxClearingError(
            "Read-only reconciliation must not authorize payments"
        )

    return cleared



async def advance_tax_remittance_commitment(
    db,
    *,
    company_id: int,
    payroll_advance_id: int,
    component: str,
    as_of: date,
) -> Decimal:
    """Read verified, dated advance-tax bank payments.

    Reversal changes the effective balance only from its posting date.
    This function never creates or changes accounting records.
    """
    from app.models.payroll_advance_tax_remittance import (
        PayrollAdvanceTaxRemittance,
    )

    if (
        type(company_id) is not int
        or company_id <= 0
        or type(payroll_advance_id) is not int
        or payroll_advance_id <= 0
        or type(as_of) is not date
        or component not in EMPLOYEE_COMPONENTS
    ):
        raise MonthlyTaxClearingError(
            "Invalid advance tax commitment request"
        )

    advance = await db.scalar(
        select(PayrollAdvance).where(
            PayrollAdvance.company_id == company_id,
            PayrollAdvance.id == payroll_advance_id,
        )
    )
    if advance is None:
        raise MonthlyTaxClearingError(
            "Company-scoped advance not found"
        )

    proof = await verify_automatic_advance_tax_source_in_db(
        db, advance
    )
    expected = [
        Decimal(row.amount)
        for row in proof.components
        if row.component == component
    ]
    if len(expected) != 1:
        raise MonthlyTaxClearingError(
            "Verified advance tax component missing"
        )

    rows = (
        await db.scalars(
            select(PayrollAdvanceTaxRemittance).where(
                PayrollAdvanceTaxRemittance.company_id == company_id,
                PayrollAdvanceTaxRemittance.payroll_advance_id
                    == payroll_advance_id,
                PayrollAdvanceTaxRemittance.component == component,
            )
        )
    ).all()

    if len(rows) > 1:
        raise MonthlyTaxClearingError(
            "Duplicate advance tax attribution"
        )

    if not rows:
        return Decimal("0.00")

    row = rows[0]
    original = await db.scalar(
        select(JournalEntry).where(
            JournalEntry.company_id == company_id,
            JournalEntry.id == row.journal_entry_id,
        )
    )
    if (
        original is None
        or original.reversal_of_id is not None
        or original.status not in ("posted", "reversed")
        or original.entry_date != advance.payment_date
        or Decimal(row.amount) != expected[0]
    ):
        raise MonthlyTaxClearingError(
            "Advance tax payment evidence inconsistent"
        )

    reversals = (
        await db.scalars(
            select(JournalEntry).where(
                JournalEntry.company_id == company_id,
                JournalEntry.reversal_of_id == original.id,
            )
        )
    ).all()

    if len(reversals) > 1:
        raise MonthlyTaxClearingError(
            "Multiple advance tax payment reversals"
        )

    if reversals:
        reversal = reversals[0]
        if (
            reversal.status != "posted"
            or reversal.entry_date < original.entry_date
        ):
            raise MonthlyTaxClearingError(
                "Invalid dated advance tax reversal"
            )
        if original.status != "reversed":
            raise MonthlyTaxClearingError(
                "Advance tax reversal state inconsistent"
            )
        if reversal.entry_date <= as_of:
            return Decimal("0.00")
    elif original.status != "posted":
        raise MonthlyTaxClearingError(
            "Reversed advance tax payment has no reversal journal"
        )

    if original.entry_date > as_of:
        return Decimal("0.00")

    return Decimal(row.amount)



async def reconcile_combined_advance_monthly_tax_from_db(
    db,
    *,
    company_id: int,
    payroll_advance_id: int,
    payroll_calculation_id: int,
    as_of: date,
):
    """Read-only combined settlement with explicit overpayment refusal.

    Never silently cap a paid amount to the monthly liability.
    """
    from app.models.payroll_statutory import (
        PayrollStatutoryResult,
        PayrollStatutoryResultLine,
    )
    from app.models.payroll import PayrollCalculation


    if (
        type(company_id) is not int or company_id <= 0
        or type(payroll_advance_id) is not int
        or payroll_advance_id <= 0
        or type(payroll_calculation_id) is not int
        or payroll_calculation_id <= 0
        or type(as_of) is not date
    ):
        raise MonthlyTaxClearingError(
            "Invalid combined tax settlement request"
        )

    advance = await db.scalar(
        select(PayrollAdvance).where(
            PayrollAdvance.company_id == company_id,
            PayrollAdvance.id == payroll_advance_id,
        )
    )
    calculation = await db.scalar(
        select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id == payroll_calculation_id,
        )
    )

    if (
        advance is None or calculation is None
        or advance.payroll_period_id
            != calculation.payroll_period_id
        or advance.employment_contract_id
            != calculation.employment_contract_id
    ):
        raise MonthlyTaxClearingError(
            "Combined payroll sources do not match"
        )

    if as_of < advance.payment_date:
        raise MonthlyTaxClearingError(
            "Combined settlement predates advance payment"
        )

    # Reuse existing dated payroll/advance posting verification,
    # including monthly statutory amount and financial source checks.
    existing = await reconcile_monthly_advance_taxes_from_db(
        db,
        company_id=company_id,
        payroll_advance_id=payroll_advance_id,
        payroll_calculation_id=payroll_calculation_id,
        as_of=as_of,
    )

    liability = {
        key: Decimal(existing.monthly_tax_liability[key])
        for key in EMPLOYEE_COMPONENTS
    }
    monthly = {
        key: Decimal(existing.tax_actually_remitted[key])
        for key in EMPLOYEE_COMPONENTS
    }
    advance_paid = {}

    for key in EMPLOYEE_COMPONENTS:
        advance_paid[key] = await advance_tax_remittance_commitment(
            db,
            company_id=company_id,
            payroll_advance_id=payroll_advance_id,
            component=key,
            as_of=as_of,
        )

    total = {
        key: monthly[key] + advance_paid[key]
        for key in EMPLOYEE_COMPONENTS
    }

    for key in EMPLOYEE_COMPONENTS:
        if total[key] > liability[key]:
            raise MonthlyTaxClearingError(
                "Combined advance and monthly tax remittances "
                "exceed recognized monthly liability: " + key
            )

    return CombinedTaxSettlement(
        monthly_liability=liability,
        advance_remitted=advance_paid,
        monthly_remitted=monthly,
        total_remitted=total,
        unpaid={
            key: liability[key] - total[key]
            for key in EMPLOYEE_COMPONENTS
        },
    )
