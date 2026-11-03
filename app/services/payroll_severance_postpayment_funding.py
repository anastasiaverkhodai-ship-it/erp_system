"""Dated evidence of real payroll payments and attributed tax remittances.

Amounts are for the WHOLE payroll calculation, not severance-only.
A posted accounting journal is evidence of recognized accounting
activity, not independent confirmation of an external bank transfer.

Advance taxes, payroll deductions and actual employee returns require
separate reconciliation before an executable settlement is possible.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import aliased

from app.models.journal_entry import JournalEntry
from app.models.payroll import PayrollCalculation
from app.models.payroll_disbursement import PayrollDisbursement
from app.models.payroll_tax_remittance import (
    PayrollTaxRemittance,
    PayrollTaxRemittanceLine,
)

from app.services.payroll_severance_net_tax_settlement import amount


ZERO = Decimal("0.00")

COMPONENTS = (
    "personal_income_tax",
    "military_levy",
    "unified_social_contribution",
)


class SeveranceFundingEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class PostedPayrollFunding:
    company_id: int
    payroll_calculation_id: int
    as_of: date
    currency_code: str
    posted_payment_count: int
    posted_employee_amount: Decimal
    posted_withheld_amount: Decimal
    posted_payment_total: Decimal
    remitted_personal_income_tax: Decimal
    remitted_military_levy: Decimal
    remitted_employer_contribution: Decimal

    # These are deliberately always false.
    severance_tax_attribution_verified: bool = False
    advance_tax_reconciliation_complete: bool = False
    employee_recovery_verified: bool = False
    financial_execution_authorized: bool = False


def validate_funding_evidence(result):
    if not isinstance(result, PostedPayrollFunding):
        raise SeveranceFundingEvidenceError(
            "Typed posted-payroll evidence required"
        )

    fields = (
        "posted_employee_amount",
        "posted_withheld_amount",
        "posted_payment_total",
        "remitted_personal_income_tax",
        "remitted_military_levy",
        "remitted_employer_contribution",
    )

    try:
        values = {
            key: amount(
                getattr(result, key),
                key,
                nonnegative=True,
            )
            for key in fields
        }
    except (ValueError, ArithmeticError) as exc:
        raise SeveranceFundingEvidenceError(
            "Invalid financial evidence money"
        ) from exc

    if (
        values["posted_employee_amount"]
        + values["posted_withheld_amount"]
        != values["posted_payment_total"]
    ):
        raise SeveranceFundingEvidenceError(
            "Posted payroll payment totals do not reconcile"
        )

    if (
        type(result.posted_payment_count) is not int
        or result.posted_payment_count < 0
        or result.currency_code != "UAH"
    ):
        raise SeveranceFundingEvidenceError(
            "Invalid posted payroll identity or currency"
        )

    if (
        result.severance_tax_attribution_verified
        or result.advance_tax_reconciliation_complete
        or result.employee_recovery_verified
        or result.financial_execution_authorized
    ):
        raise SeveranceFundingEvidenceError(
            "Evidence cannot grant financial execution authorization"
        )

    return result


def effective_original_journal(as_of):
    """Original journals that remain effective at the requested date."""
    reversal = aliased(JournalEntry)

    reversed_by_date = (
        select(reversal.id)
        .where(
            reversal.company_id == JournalEntry.company_id,
            reversal.reversal_of_id == JournalEntry.id,
            reversal.status == "posted",
            reversal.entry_date <= as_of,
        )
        .exists()
    )

    return (
        JournalEntry.reversal_of_id.is_(None),
        JournalEntry.status.in_(("posted", "reversed")),
        JournalEntry.entry_date <= as_of,
        ~reversed_by_date,
    )


async def load_posted_payroll_funding(
    db,
    *,
    company_id,
    payroll_calculation_id,
    as_of,
):
    if (
        type(company_id) is not int
        or company_id <= 0
        or type(payroll_calculation_id) is not int
        or payroll_calculation_id <= 0
        or type(as_of) is not date
    ):
        raise SeveranceFundingEvidenceError(
            "Valid company, calculation and date required"
        )

    calculation = await db.scalar(
        select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id == payroll_calculation_id,
        )
    )

    if calculation is None or calculation.currency_code != "UAH":
        raise SeveranceFundingEvidenceError(
            "UAH payroll calculation not found"
        )

    payment_rows = (
        await db.execute(
            select(
                PayrollDisbursement.amount,
                PayrollDisbursement.withheld_amount,
            )
            .join(
                JournalEntry,
                and_(
                    JournalEntry.company_id
                    == PayrollDisbursement.company_id,
                    JournalEntry.payroll_disbursement_id
                    == PayrollDisbursement.id,
                    JournalEntry.reversal_of_id.is_(None),
                ),
            )
            .where(
                PayrollDisbursement.company_id == company_id,
                PayrollDisbursement.payroll_calculation_id
                == payroll_calculation_id,
                PayrollDisbursement.payment_date <= as_of,
                *effective_original_journal(as_of),
            )
        )
    ).all()

    employee = sum(
        (
            amount(row[0], "payment", nonnegative=True)
            for row in payment_rows
        ),
        ZERO,
    )

    withheld = sum(
        (
            amount(row[1], "withheld", nonnegative=True)
            for row in payment_rows
        ),
        ZERO,
    )

    tax_rows = (
        await db.execute(
            select(
                PayrollTaxRemittance.component,
                PayrollTaxRemittanceLine.amount,
            )
            .join(
                PayrollTaxRemittanceLine,
                and_(
                    PayrollTaxRemittanceLine.company_id
                    == PayrollTaxRemittance.company_id,
                    PayrollTaxRemittanceLine.remittance_id
                    == PayrollTaxRemittance.id,
                ),
            )
            .join(
                JournalEntry,
                and_(
                    JournalEntry.company_id
                    == PayrollTaxRemittance.company_id,
                    JournalEntry.id
                    == PayrollTaxRemittance.journal_entry_id,
                ),
            )
            .where(
                PayrollTaxRemittance.company_id == company_id,
                PayrollTaxRemittanceLine.payroll_calculation_id
                == payroll_calculation_id,
                *effective_original_journal(as_of),
            )
        )
    ).all()

    taxes = {key: ZERO for key in COMPONENTS}

    for component, value in tax_rows:
        if component not in taxes:
            raise SeveranceFundingEvidenceError(
                "Unsupported tax remittance component"
            )

        taxes[component] += amount(
            value,
            "tax remittance",
            nonnegative=True,
        )

    return validate_funding_evidence(
        PostedPayrollFunding(
            company_id=company_id,
            payroll_calculation_id=payroll_calculation_id,
            as_of=as_of,
            currency_code="UAH",
            posted_payment_count=len(payment_rows),
            posted_employee_amount=employee,
            posted_withheld_amount=withheld,
            posted_payment_total=employee + withheld,
            remitted_personal_income_tax=taxes[
                "personal_income_tax"
            ],
            remitted_military_levy=taxes["military_levy"],
            remitted_employer_contribution=taxes[
                "unified_social_contribution"
            ],
        )
    )
