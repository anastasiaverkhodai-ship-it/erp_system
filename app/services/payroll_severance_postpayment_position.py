"""Source-bound whole-payroll financial position for severance review.

The outstanding whole-payroll net is NOT severance-only employee debt.
This module does not grant correction, payment or posting authority.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.services.payroll_severance_dated_tax_allocation import (
    COMPONENTS,
    exact_money,
)
from app.services.payroll_severance_mixed_financial_snapshot import (
    SeveranceMixedFinancialPreview,
)
from app.services.payroll_severance_postpayment_funding import (
    PostedPayrollFunding,
    validate_funding_evidence,
)
from app.services.payroll_severance_postpayment_assessment import (
    SeveranceSettlementAssessment,
)
from app.services.payroll_severance_dated_tax_allocation import (
    SeveranceAllocationReview,
)


class SeverancePositionError(ValueError):
    pass


@dataclass(frozen=True)
class SeverancePostpaymentPosition:
    company_id: int
    payroll_calculation_id: int
    payroll_gross: Decimal
    payroll_net: Decimal
    employee_posted: Decimal
    employee_remaining_whole_payroll_ceiling: Decimal
    remitted_taxes: tuple[tuple[str, Decimal], ...]
    unremitted_taxes: tuple[tuple[str, Decimal], ...]
    posted_payment_count: int
    reconciled: bool
    severance_amount_executable: bool = False
    employee_recovery_authorized: bool = False
    tax_correction_authorized: bool = False
    journal_posting_authorized: bool = False


def reconcile_severance_postpayment_position(
    *,
    preview,
    review,
    funding,
    assessment,
):
    if (
        not isinstance(preview, SeveranceMixedFinancialPreview)
        or not isinstance(review, SeveranceAllocationReview)
        or not isinstance(funding, PostedPayrollFunding)
        or not isinstance(assessment, SeveranceSettlementAssessment)
    ):
        raise SeverancePositionError(
            "Persisted-source financial evidence is required"
        )

    if (
        not (
            preview.company_id
            == review.company_id
            == funding.company_id
            == assessment.company_id
        )
        or not (
            preview.payroll_calculation_id
            == review.payroll_calculation_id
            == funding.payroll_calculation_id
        )
    ):
        raise SeverancePositionError(
            "Company or payroll calculation scope mismatch"
        )

    if (
        funding.currency_code != "UAH"
        or review.currency_code != "UAH"
        or funding.as_of < review.income_date
        or not review.reconciled
        or not preview.statutory_totals_reconciled
    ):
        raise SeverancePositionError(
            "Missing dated financial reconciliation"
        )

    validate_funding_evidence(funding)
    preview.financial.validate()

    if (
        preview.allocation_evidence_approved
        or preview.payment_authorized
        or preview.tax_correction_authorized
        or preview.journal_posting_authorized
        or funding.financial_execution_authorized
        or review.financial_execution_authorized
        or review.independent_evidence_approved
    ):
        raise SeverancePositionError(
            "Review objects cannot grant execution approval"
        )

    if (
        funding.posted_payment_count
        != assessment.posted_disbursement_count
        or funding.posted_payment_total
        != assessment.posted_disbursement_total
    ):
        raise SeverancePositionError(
            "Dated payment evidence and assessment disagree"
        )

    payroll_gross = (
        exact_money(preview.financial.gross)
        + exact_money(preview.ordinary_gross)
    )

    if {
        row.component for row in review.allocations
    } != COMPONENTS or len(review.allocations) != 3:
        raise SeverancePositionError(
            "Incomplete statutory tax allocation"
        )

    totals = {
        row.component: exact_money(row.statutory_total)
        for row in review.allocations
    }

    remitted = {
        "personal_income_tax": exact_money(
            funding.remitted_personal_income_tax
        ),
        "military_levy": exact_money(
            funding.remitted_military_levy
        ),
        "unified_social_contribution": exact_money(
            funding.remitted_employer_contribution
        ),
    }

    if any(
        remitted[component] > totals[component]
        for component in COMPONENTS
    ):
        raise SeverancePositionError(
            "Tax remittances exceed original statutory totals"
        )

    payroll_net = (
        payroll_gross
        - totals["personal_income_tax"]
        - totals["military_levy"]
    )

    if payroll_net < 0:
        raise SeverancePositionError(
            "Negative whole-payroll statutory net"
        )

    paid = exact_money(funding.posted_payment_total)

    if paid > payroll_net:
        raise SeverancePositionError(
            "Payments exceed whole-payroll statutory net"
        )

    remaining = payroll_net - paid

    return SeverancePostpaymentPosition(
        company_id=preview.company_id,
        payroll_calculation_id=preview.payroll_calculation_id,
        payroll_gross=payroll_gross,
        payroll_net=payroll_net,
        employee_posted=paid,
        employee_remaining_whole_payroll_ceiling=remaining,
        remitted_taxes=tuple(
            sorted(remitted.items())
        ),
        unremitted_taxes=tuple(
            sorted(
                (
                    component,
                    totals[component] - remitted[component],
                )
                for component in COMPONENTS
            )
        ),
        posted_payment_count=funding.posted_payment_count,
        reconciled=True,
    )
