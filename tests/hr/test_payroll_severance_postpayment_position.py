from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest

from app.services.payroll_severance_postpayment_funding import (
    PostedPayrollFunding,
)
from app.services.payroll_severance_postpayment_assessment import (
    SeveranceSettlementAssessment,
)
from app.services.payroll_severance_mixed_financial_snapshot import (
    SeveranceMixedFinancialPreview,
)
from app.services.payroll_severance_net_tax_settlement import (
    SeveranceFinancialSnapshot,
)
from app.services.payroll_severance_dated_tax_allocation import (
    ComponentAllocation,
    build_dated_severance_allocation_review,
)
from app.services.payroll_severance_postpayment_position import (
    SeverancePositionError,
    reconcile_severance_postpayment_position,
)


def fixtures():
    rows = [
        ComponentAllocation(
            component=name,
            statutory_total=total,
            ordinary_amount=total-severance,
            severance_amount=severance,
            evidence_reference="UNIT-TEST-NOT-APPROVED",
        )
        for name, total, severance in (
            ("personal_income_tax", D("2347.20"), D("1800.00")),
            ("military_levy", D("652.00"), D("500.00")),
            ("unified_social_contribution", D("228.80"), D("0.00")),
        )
    ]

    review = build_dated_severance_allocation_review(
        company_id=1,
        payroll_calculation_id=2,
        income_date=date(2026, 9, 15),
        currency_code="UAH",
        rows=rows,
    )
    preview = SeveranceMixedFinancialPreview(
        company_id=1,
        payroll_calculation_id=2,
        severance_source_ids=(3,),
        ordinary_gross=D("3040.00"),
        financial=SeveranceFinancialSnapshot(
            gross=D("10000.00"),
            personal_income_tax=D("1800.00"),
            military_levy=D("500.00"),
            employer_contribution=D("0.00"),
            net=D("7700.00"),
        ),
        statutory_totals_reconciled=True,
    )
    funding = PostedPayrollFunding(
        company_id=1,
        payroll_calculation_id=2,
        as_of=date(2026, 9, 30),
        currency_code="UAH",
        posted_payment_count=2,
        posted_employee_amount=D("10040.80"),
        posted_withheld_amount=D("0.00"),
        posted_payment_total=D("10040.80"),
        remitted_personal_income_tax=D("2347.20"),
        remitted_military_levy=D("652.00"),
        remitted_employer_contribution=D("228.80"),
    )
    assessment = SeveranceSettlementAssessment(
        company_id=1,
        payroll_input_id=4,
        latest_source_id=3,
        last_calculated_source_id=3,
        original_gross=D("10000.00"),
        latest_gross=D("10000.00"),
        gross_difference=D("0.00"),
        posted_disbursement_count=2,
        posted_disbursement_total=D("10040.80"),
        classification="POSTED_AMOUNT_UNCHANGED",
        review_required=True,
    )
    return preview, review, funding, assessment


def calculate(values):
    preview, review, funding, assessment = values
    return reconcile_severance_postpayment_position(
        preview=preview,
        review=review,
        funding=funding,
        assessment=assessment,
    )


def test_complete_whole_payroll_reconciliation():
    result = calculate(fixtures())
    assert result.payroll_gross == D("13040.00")
    assert result.payroll_net == D("10040.80")
    assert result.employee_remaining_whole_payroll_ceiling == 0
    assert result.reconciled
    assert not result.journal_posting_authorized
    assert not result.severance_amount_executable


def test_partial_payment_is_not_severance_debt():
    preview, review, funding, assessment = fixtures()
    funding = replace(
        funding,
        posted_payment_count=1,
        posted_employee_amount=D("5000.00"),
        posted_payment_total=D("5000.00"),
    )
    assessment = replace(
        assessment,
        posted_disbursement_count=1,
        posted_disbursement_total=D("5000.00"),
    )
    result = calculate((preview, review, funding, assessment))
    assert result.employee_remaining_whole_payroll_ceiling == D("5040.80")
    assert not result.severance_amount_executable


def test_mismatched_payment_history_rejected():
    preview, review, funding, assessment = fixtures()
    with pytest.raises(
        SeverancePositionError,
        match="disagree",
    ):
        calculate((
            preview,
            review,
            funding,
            replace(
                assessment,
                posted_disbursement_total=D("5000.00"),
            ),
        ))


def test_tax_overallocation_rejected():
    preview, review, funding, assessment = fixtures()
    funding = replace(
        funding,
        remitted_military_levy=D("652.01"),
    )
    with pytest.raises(
        SeverancePositionError,
        match="exceed",
    ):
        calculate((preview, review, funding, assessment))


def test_different_company_rejected():
    preview, review, funding, assessment = fixtures()
    with pytest.raises(
        SeverancePositionError,
        match="scope mismatch",
    ):
        calculate((
            preview, review,
            replace(funding, company_id=99),
            assessment,
        ))


def test_no_forged_execution_permission():
    preview, review, funding, assessment = fixtures()
    with pytest.raises(SeverancePositionError):
        calculate((
            replace(preview, payment_authorized=True),
            review,
            funding,
            assessment,
        ))
