"""Source-bound severance financial snapshot for mixed payroll.

A reconciled, user-supplied allocation is a review artifact, not
independent evidence of the correct income-specific tax treatment.

This service verifies the review against persisted PostgreSQL payroll
and statutory totals. It never authorizes journal posting, payment,
employee recovery, tax refund, or statutory reporting.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from app.models.payroll import (
    PayrollCalculation,
    PayrollCalculationLine,
)
from app.models.payroll_statutory import (
    PayrollStatutoryResult,
    PayrollStatutoryResultLine,
)
from app.services.payroll_severance_income_classification import (
    split_payroll_income,
)
from app.services.payroll_severance_dated_tax_allocation import (
    COMPONENTS,
    SeveranceAllocationReview,
    exact_money,
)
from app.services.payroll_severance_net_tax_settlement import (
    SeveranceFinancialSnapshot,
)


class SeveranceFinancialSourceError(ValueError):
    pass


@dataclass(frozen=True)
class SeveranceMixedFinancialPreview:
    company_id: int
    payroll_calculation_id: int
    severance_source_ids: tuple[int, ...]
    ordinary_gross: Decimal
    financial: SeveranceFinancialSnapshot
    statutory_totals_reconciled: bool
    allocation_evidence_approved: bool = False
    payment_authorized: bool = False
    tax_correction_authorized: bool = False
    journal_posting_authorized: bool = False


def bind_review_to_statutory_totals(
    *,
    review,
    company_id,
    calculation_id,
    statutory_amounts,
    severance_gross,
    ordinary_gross,
    source_ids,
):
    """Validate persisted totals and produce an unapproved preview.

    The allocation's independent legal/accounting basis is NOT
    established by equality with aggregate statutory totals.
    """
    if not isinstance(review, SeveranceAllocationReview):
        raise SeveranceFinancialSourceError(
            "Typed dated allocation review required"
        )

    if (
        type(company_id) is not int
        or company_id <= 0
        or type(calculation_id) is not int
        or calculation_id <= 0
        or review.company_id != company_id
        or review.payroll_calculation_id != calculation_id
    ):
        raise SeveranceFinancialSourceError(
            "Allocation does not belong to this company/calculation"
        )

    if (
        not review.reconciled
        or review.currency_code != "UAH"
        or review.independent_evidence_approved
        or review.financial_execution_authorized
        or review.statutory_correction_authorized
    ):
        raise SeveranceFinancialSourceError(
            "Only unapproved UAH allocation reviews are accepted"
        )

    if type(source_ids) is not tuple or not source_ids:
        raise SeveranceFinancialSourceError(
            "Documented severance income source required"
        )

    if any(
        type(source_id) is not int or source_id <= 0
        for source_id in source_ids
    ) or len(set(source_ids)) != len(source_ids):
        raise SeveranceFinancialSourceError(
            "Invalid or duplicate severance source IDs"
        )

    if (
        not isinstance(statutory_amounts, dict)
        or set(statutory_amounts) != COMPONENTS
    ):
        raise SeveranceFinancialSourceError(
            "All three persisted statutory components required"
        )

    gross = exact_money(severance_gross)
    ordinary = exact_money(ordinary_gross)

    if gross <= 0:
        raise SeveranceFinancialSourceError(
            "Positive documented severance gross required"
        )

    if (
        len(review.allocations) != 3
        or {item.component for item in review.allocations}
        != COMPONENTS
    ):
        raise SeveranceFinancialSourceError(
            "Incomplete or duplicate reviewed allocation"
        )

    amounts = {}

    for item in review.allocations:
        persisted = exact_money(
            statutory_amounts[item.component]
        )

        total = exact_money(item.statutory_total)
        ordinary_amount = exact_money(item.ordinary_amount)
        severance_amount = exact_money(item.severance_amount)

        if (
            persisted != total
            or ordinary_amount + severance_amount != total
        ):
            raise SeveranceFinancialSourceError(
                f"Persisted statutory mismatch: {item.component}"
            )

        amounts[item.component] = severance_amount

    pit = amounts["personal_income_tax"]
    levy = amounts["military_levy"]
    contribution = amounts["unified_social_contribution"]

    financial = SeveranceFinancialSnapshot(
        gross=gross,
        personal_income_tax=pit,
        military_levy=levy,
        net=gross - pit - levy,
        employer_contribution=contribution,
    )

    financial.validate()

    return SeveranceMixedFinancialPreview(
        company_id=company_id,
        payroll_calculation_id=calculation_id,
        severance_source_ids=source_ids,
        ordinary_gross=ordinary,
        financial=financial,
        statutory_totals_reconciled=True,
    )



def verify_statutory_financial_integrity(
    *,
    gross_amount,
    statutory_header,
    component_totals,
):
    """Reconcile persisted statutory header and component totals.

    This is an arithmetic integrity control, not tax allocation
    approval or permission to execute settlement transactions.
    """
    from app.services.payroll_severance_dated_tax_allocation import (
        COMPONENTS,
        exact_money,
    )

    if (
        not isinstance(component_totals, dict)
        or set(component_totals) != COMPONENTS
    ):
        raise SeveranceFinancialSourceError(
            "Exactly three statutory component totals required"
        )

    try:
        gross = exact_money(gross_amount)

        header_gross = exact_money(
            statutory_header.gross_amount
        )
        header_withheld = exact_money(
            statutory_header.employee_withholding_amount
        )
        header_contribution = exact_money(
            statutory_header.employer_contribution_amount
        )
        header_net = exact_money(
            statutory_header.net_amount
        )

        pit = exact_money(
            component_totals["personal_income_tax"]
        )
        levy = exact_money(
            component_totals["military_levy"]
        )
        contribution = exact_money(
            component_totals["unified_social_contribution"]
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise SeveranceFinancialSourceError(
            "Invalid persisted statutory financial amounts"
        ) from exc

    if header_gross != gross:
        raise SeveranceFinancialSourceError(
            "Payroll and statutory gross mismatch"
        )

    if header_withheld != pit + levy:
        raise SeveranceFinancialSourceError(
            "Statutory withholding does not reconcile"
        )

    if header_contribution != contribution:
        raise SeveranceFinancialSourceError(
            "Statutory employer contribution does not reconcile"
        )

    if header_net != gross - header_withheld:
        raise SeveranceFinancialSourceError(
            "Statutory net does not reconcile"
        )

    return True


async def load_mixed_severance_financial_preview(
    db,
    *,
    company_id,
    payroll_calculation_id,
    review,
):
    """Read persisted sources; return a non-executable financial preview."""

    if (
        type(company_id) is not int
        or company_id <= 0
        or type(payroll_calculation_id) is not int
        or payroll_calculation_id <= 0
    ):
        raise SeveranceFinancialSourceError(
            "Invalid financial source identity"
        )

    calculation = await db.scalar(
        select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id == payroll_calculation_id,
        )
    )

    if calculation is None:
        raise SeveranceFinancialSourceError(
            "Payroll calculation not found"
        )

    if calculation.currency_code != "UAH":
        raise SeveranceFinancialSourceError(
            "UAH payroll required"
        )

    income_lines = list((
        await db.scalars(
            select(PayrollCalculationLine).where(
                PayrollCalculationLine.company_id == company_id,
                PayrollCalculationLine.payroll_calculation_id
                == payroll_calculation_id,
            )
        )
    ).all())

    split = split_payroll_income(
        gross_amount=calculation.gross_amount,
        currency_code=calculation.currency_code,
        lines=income_lines,
    )

    statutory = await db.scalar(
        select(PayrollStatutoryResult).where(
            PayrollStatutoryResult.company_id == company_id,
            PayrollStatutoryResult.payroll_calculation_id
            == payroll_calculation_id,
        )
    )

    if statutory is None or statutory.currency_code != "UAH":
        raise SeveranceFinancialSourceError(
            "UAH statutory result required"
        )

    if Decimal(statutory.gross_amount) != split.total_gross:
        raise SeveranceFinancialSourceError(
            "Statutory gross differs from payroll gross"
        )

    statutory_lines = list((
        await db.scalars(
            select(PayrollStatutoryResultLine).where(
                PayrollStatutoryResultLine.company_id == company_id,
                PayrollStatutoryResultLine.payroll_statutory_result_id
                == statutory.id,
            )
        )
    ).all())

    totals = {}

    for line in statutory_lines:
        if (
            line.component not in COMPONENTS
            or line.component in totals
            or line.currency_code != "UAH"
        ):
            raise SeveranceFinancialSourceError(
                "Unsupported or duplicate statutory component"
            )

        totals[line.component] = line.amount

    if set(totals) != COMPONENTS:
        raise SeveranceFinancialSourceError(
            "Incomplete persisted statutory components"
        )

    verify_statutory_financial_integrity(
        gross_amount=split.total_gross,
        statutory_header=statutory,
        component_totals=totals,
    )

    if (
        review is None
        or review.income_date is None
    ):
        raise SeveranceFinancialSourceError(
            "Dated allocation review required"
        )

    return bind_review_to_statutory_totals(
        review=review,
        company_id=company_id,
        calculation_id=payroll_calculation_id,
        statutory_amounts=totals,
        severance_gross=split.severance_gross,
        ordinary_gross=split.ordinary_gross,
        source_ids=split.severance_source_ids,
    )
