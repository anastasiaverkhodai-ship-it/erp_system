"""Strict internal D1 source completeness and D5/D6 readiness.

Only verifies traceability and coverage of existing payroll evidence.
Does not assert statutory D1/D5/D6 mapping, legal applicability,
tax base equivalence or filing readiness.
"""

from datetime import date
from decimal import Decimal, InvalidOperation

from app.services.payroll_regulatory_d1_dated_sources import (
    build_d1_dated_source_review,
)


class D1CompletenessError(ValueError):
    pass


ZERO = Decimal("0.00")


def money(value):
    if value is None or isinstance(value, bool):
        raise D1CompletenessError("Invalid monetary amount")

    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise D1CompletenessError(
            "Invalid monetary amount"
        ) from exc

    if not result.is_finite():
        raise D1CompletenessError(
            "Non-finite monetary amount"
        )

    if result.as_tuple().exponent < -2:
        raise D1CompletenessError(
            "Invalid monetary precision"
        )

    return result


def positive_id(value, name):
    if type(value) is not int or value <= 0:
        raise D1CompletenessError(
            "Invalid " + name
        )
    return value


def parse_date(value):
    if not isinstance(value, str):
        raise D1CompletenessError("Invalid dated source date")

    try:
        result = date.fromisoformat(value)
    except ValueError as exc:
        raise D1CompletenessError(
            "Invalid dated source date"
        ) from exc

    if result.isoformat() != value:
        raise D1CompletenessError(
            "Non-canonical dated source date"
        )

    return result


def strict_d1_completeness(review):
    """Reject missing, duplicate or contradictory persisted provenance.

    The sum of dated source amounts is not automatically required to
    equal the final USC assessment base: minimum/maximum bases and
    statutory adjustments can affect that relationship.
    """

    if not isinstance(review, dict):
        raise D1CompletenessError("D1 review required")

    if review.get("report_kind") != "d1":
        raise D1CompletenessError("Wrong report kind")

    company_id = positive_id(
        review.get("company_id"), "company"
    )
    period_id = positive_id(
        review.get("payroll_period_id"), "period"
    )

    if review.get("review_only") is not True:
        raise D1CompletenessError(
            "Unverified D1 review status"
        )

    if review.get("official_statutory_generation") != "BLOCKED":
        raise D1CompletenessError(
            "Official statutory gate must remain blocked"
        )

    if review.get("d1_financial_reconciliation") != "PASS":
        raise D1CompletenessError(
            "D1 financial reconciliation missing"
        )

    controls = review.get("dated_controls")

    if not isinstance(controls, list) or not controls:
        raise D1CompletenessError(
            "D1 statutory line controls required"
        )

    if review.get("dated_statutory_line_count") != len(controls):
        raise D1CompletenessError(
            "D1 statutory line count mismatch"
        )

    seen_statutory = set()
    seen_sources = set()
    seen_calculations = set()
    missing = []
    total_usc = ZERO
    source_count = 0

    for control in controls:
        if not isinstance(control, dict):
            raise D1CompletenessError(
                "Invalid D1 control"
            )

        line_id = positive_id(
            control.get("statutory_line_id"),
            "statutory line",
        )

        calculation_id = positive_id(
            control.get("payroll_calculation_id"),
            "calculation",
        )

        if line_id in seen_statutory:
            raise D1CompletenessError(
                "Duplicate statutory line control"
            )

        seen_statutory.add(line_id)
        seen_calculations.add(calculation_id)

        contribution = money(
            control.get("statutory_contribution_amount")
        )

        if contribution < ZERO:
            raise D1CompletenessError(
                "Negative D1 contribution"
            )

        total_usc += contribution

        sources = control.get("dated_sources")

        if not isinstance(sources, list):
            raise D1CompletenessError(
                "Dated source list required"
            )

        if control.get("dated_source_count") != len(sources):
            raise D1CompletenessError(
                "Dated source count mismatch"
            )

        source_total = ZERO

        for item in sources:
            if not isinstance(item, dict):
                raise D1CompletenessError(
                    "Invalid dated source item"
                )

            identifier = positive_id(
                item.get("source_id"),
                "dated source",
            )

            positive_id(
                item.get("payroll_calculation_line_id"),
                "calculation line",
            )

            if identifier in seen_sources:
                raise D1CompletenessError(
                    "Duplicate dated source across D1 lines"
                )

            seen_sources.add(identifier)
            source_count += 1

            start = parse_date(
                item.get("income_effective_from")
            )
            end = parse_date(
                item.get("income_effective_to")
            )

            if start > end:
                raise D1CompletenessError(
                    "Invalid income date interval"
                )

            amount = money(
                item.get("source_amount")
            )

            if amount < ZERO:
                raise D1CompletenessError(
                    "Negative dated source"
                )

            source_total += amount

        if source_total != money(
            control.get("dated_source_amount_total")
        ):
            raise D1CompletenessError(
                "Dated source amount total mismatch"
            )

        expected_coverage = (
            "present" if sources else "missing"
        )

        if (
            control.get("dated_source_coverage")
            != expected_coverage
        ):
            raise D1CompletenessError(
                "Dated provenance status mismatch"
            )

        if not sources:
            missing.append(line_id)

    if total_usc != money(
        review.get("employer_contribution_total")
    ):
        raise D1CompletenessError(
            "D1 USC reconciliation mismatch"
        )

    if review.get("missing_dated_source_count") != len(missing):
        raise D1CompletenessError(
            "Missing-source counter mismatch"
        )

    expected_status = (
        "COMPLETE" if not missing else "INCOMPLETE"
    )

    if review.get("dated_provenance_status") != expected_status:
        raise D1CompletenessError(
            "Dated provenance aggregate status mismatch"
        )

    # COMPLETE here means every frozen USC line has at least
    # one internally traceable dated source. It is not proof of
    # statutory base allocation, effective legal classifications,
    # coverage of all HR events or form readiness.
    status = (
        "SOURCE_COVERAGE_PRESENT"
        if not missing
        else "SOURCE_COVERAGE_INCOMPLETE"
    )

    return {
        "company_id": company_id,
        "payroll_period_id": period_id,
        "report_kind": "d1",
        "review_only": True,
        "official_statutory_generation": "BLOCKED",
        "source_integrity": "PASS",
        "d1_status": status,
        "statutory_line_count": len(seen_statutory),
        "dated_source_count": source_count,
        "calculation_count": len(seen_calculations),
        "missing_source_line_ids": sorted(missing),
        "employer_contribution_total": str(total_usc),
        "statutory_base_allocation_verified": False,
    }


async def build_d1_completeness_control(
    db,
    *,
    company_id,
    payroll_period_id,
):
    """Run independent PostgreSQL D1 reconciliation and coverage."""

    review = await build_d1_dated_source_review(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
    )

    result = strict_d1_completeness(review)

    return {
        "d1": result,
        "d5": {
            "status": "NOT_VERIFIED",
            "reason": (
                "Independent HR event source coverage "
                "and approved event mapping required"
            ),
        },
        "d6": {
            "status": "NOT_VERIFIED",
            "reason": (
                "Independent D6 applicability, "
                "source coverage and approved mapping required"
            ),
        },
        "overall_status": "REVIEW_ONLY",
        "official_statutory_generation": "BLOCKED",
    }
