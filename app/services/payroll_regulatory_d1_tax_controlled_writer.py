"""Fail-closed D1 statutory review gate for internal reconciliation.

Uses the existing independently verified D1 payroll review.

Checks:
- report tenant and payroll period
- independently reviewed payroll source coverage
- gross total against current calculations
- employee withholding and employer USC aggregate evidence
- official generation remains blocked

This does NOT verify PIT and military levy separately,
payment evidence, HR source applicability, or official mappings.

The resulting financial control is review-only. It is NOT
embedded as a separately signed statutory source snapshot.
"""

from decimal import Decimal, InvalidOperation

from app.services.payroll_regulatory_d1_review_projection import (
    build_verified_d1_review,
)

from app.services.payroll_regulatory_verified_gross_writer import (
    derive_independent_gross_controls,
    persist_gross_verified_internal_four_form_review,
)


class D1StatutorySourceControlError(ValueError):
    pass


def money(value):
    if (
        value is None
        or isinstance(value, (float, bool))
    ):
        raise D1StatutorySourceControlError(
            "Unsafe statutory amount"
        )

    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise D1StatutorySourceControlError(
            "Invalid statutory amount"
        ) from None

    if not number.is_finite():
        raise D1StatutorySourceControlError(
            "Nonfinite statutory amount"
        )

    if number < 0:
        raise D1StatutorySourceControlError(
            "Negative statutory aggregate"
        )

    if number != number.quantize(Decimal("0.01")):
        raise D1StatutorySourceControlError(
            "Invalid statutory amount precision"
        )

    return number


def validate_d1_source_control(
    review,
    *,
    company_id,
    payroll_period_id,
    independent_gross,
):
    if not isinstance(review, dict):
        raise D1StatutorySourceControlError(
            "Verified D1 review missing"
        )

    if (
        review.get("company_id") != company_id
        or review.get("payroll_period_id")
            != payroll_period_id
        or review.get("report_kind") != "d1"
    ):
        raise D1StatutorySourceControlError(
            "Cross-company/period D1 source"
        )

    if (
        review.get("review_only") is not True
        or review.get("official_statutory_generation")
            != "BLOCKED"
        or review.get("approved_normative_mapping")
            is not False
    ):
        raise D1StatutorySourceControlError(
            "Unapproved statutory status escalation"
        )

    count = review.get("source_count")
    rows = review.get("rows")

    if (
        type(count) is not int
        or count <= 0
        or not isinstance(rows, list)
        or len(rows) != count
    ):
        raise D1StatutorySourceControlError(
            "Incomplete D1 source coverage"
        )

    gross = money(review.get("gross_total"))
    withholding = money(
        review.get("employee_withholding_total")
    )
    contribution = money(
        review.get("employer_contribution_total")
    )

    if gross != money(independent_gross):
        raise D1StatutorySourceControlError(
            "D1 gross differs from current payroll"
        )

    # An employee withholding aggregate is not by itself
    # evidence of separate PIT and military levy categories.
    return {
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "source_count": count,
        "gross_total": str(gross),
        "employee_withholding_total": str(withholding),
        "employer_contribution_total": str(contribution),
        "d1_source_review_checked": True,
        "gross_independently_reconciled": True,
        "separate_pit_verified": False,
        "separate_military_levy_verified": False,
        "payment_evidence_verified": False,
        "official_generation": "BLOCKED",
    }


async def persist_d1_tax_controlled_internal_review(
    db,
    *,
    company_id,
    payroll_period_id,
    payroll_period,
    report_references,
    request_key,
):
    """Require existing independently sourced D1 control before writing.

    This entrypoint does not override or weaken the underlying
    gross-controlled writer. Caller owns the transaction.
    """

    independent = await derive_independent_gross_controls(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        payroll_period=payroll_period,
        report_references=report_references,
    )

    d1_review = await build_verified_d1_review(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
    )

    try:
        gross_total = independent["D1"]["gross"]
    except (KeyError, TypeError):
        raise D1StatutorySourceControlError(
            "Independent payroll gross control missing"
        ) from None

    control = validate_d1_source_control(
        d1_review,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        independent_gross=gross_total,
    )

    # The existing strict entrypoint rechecks current source rows.
    # Its result is a persisted INTERNAL snapshot, not a filing.
    result = await persist_gross_verified_internal_four_form_review(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        payroll_period=payroll_period,
        report_references=report_references,
        request_key=request_key,
    )

    if (
        result.get("official_generation") != "BLOCKED"
        or result.get("independent_gross_verified")
            is not True
    ):
        raise D1StatutorySourceControlError(
            "Persisted writer returned unsafe verification state"
        )

    return {
        **result,
        "d1_source_control": control,
        "d1_statutory_review_verified": True,
        "d1_control_persisted_in_snapshot": False,
        "separate_pit_verified": False,
        "separate_military_levy_verified": False,
        "payment_evidence_verified": False,
        "hr_sources_verified": False,
        "official_generation": "BLOCKED",
    }
