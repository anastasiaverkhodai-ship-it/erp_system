"""PostgreSQL-backed taxpayer identity binding for internal 4DF.

A reviewed identifier is not an approval of statutory form fields.
This service never enables filing or report finalization.
"""
from calendar import monthrange
from datetime import date

from app.services.payroll_regulatory_4df_taxpayer_identity import (
    attach_reviewed_4df_taxpayer_identities,
)
from app.services.payroll_taxpayer_identity_review_service import (
    require_reviewed_identity,
)
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)


def _report_month_bounds(value):
    if (
        not isinstance(value, str)
        or len(value) != 7
        or value[4] != "-"
    ):
        raise PayrollRegulatoryReportError(
            "Invalid 4DF income month"
        )

    try:
        year = int(value[:4])
        month = int(value[5:])
        first = date(year, month, 1)
        last = date(
            year,
            month,
            monthrange(year, month)[1],
        )
    except (TypeError, ValueError) as exc:
        raise PayrollRegulatoryReportError(
            "Invalid 4DF income month"
        ) from exc

    if first.isoformat()[:7] != value:
        raise PayrollRegulatoryReportError(
            "Noncanonical 4DF income month"
        )

    return first, last


async def attach_persisted_4df_taxpayer_identities(
    db,
    *,
    company_id,
    financial_groups,
    calculation_sources,
    review_ids_by_employee,
):
    """Require an independently verified DB review per employee.

    A review must cover the complete income month; historical
    intervals crossing a change require separate handling.
    No document or personal identifier is written to logs.
    """
    if (
        not isinstance(review_ids_by_employee, dict)
        or not review_ids_by_employee
        or not isinstance(financial_groups, (list, tuple))
        or not financial_groups
        or not isinstance(calculation_sources, (list, tuple))
    ):
        raise PayrollRegulatoryReportError(
            "Complete persisted identity inputs required"
        )

    sources = {}

    for source in calculation_sources:
        if not isinstance(source, dict):
            raise PayrollRegulatoryReportError(
                "Invalid payroll identity source"
            )

        calculation_id = source.get("payroll_calculation_id")

        if (
            type(calculation_id) is not int
            or calculation_id <= 0
            or calculation_id in sources
        ):
            raise PayrollRegulatoryReportError(
                "Invalid or duplicate payroll calculation"
            )

        sources[calculation_id] = source

    employees = set()
    groups_by_employee = {}

    for group in financial_groups:
        if not isinstance(group, dict):
            raise PayrollRegulatoryReportError(
                "Invalid financial identity group"
            )

        employee_id = group.get("employee_id")

        if type(employee_id) is not int or employee_id <= 0:
            raise PayrollRegulatoryReportError(
                "Invalid employee in financial group"
            )

        if group.get("company_id") != company_id:
            raise PayrollRegulatoryReportError(
                "Cross-company financial group"
            )

        _report_month_bounds(group.get("income_month"))

        employees.add(employee_id)
        groups_by_employee.setdefault(
            employee_id, []
        ).append(group)

    if set(review_ids_by_employee) != employees:
        raise PayrollRegulatoryReportError(
            "Persisted identity reviews must cover "
            "exactly the financial-group employees"
        )

    evidence = []

    for employee_id in sorted(employees):
        review_id = review_ids_by_employee[employee_id]

        if type(review_id) is not int or review_id <= 0:
            raise PayrollRegulatoryReportError(
                "Invalid persisted identity review identifier"
            )

        employee_groups = groups_by_employee[employee_id]
        matching_sources = []

        for group in employee_groups:
            for calculation_id in group.get(
                "payroll_calculation_ids", ()
            ):
                source = sources.get(calculation_id)

                if (
                    source is None
                    or source.get("employee_id") != employee_id
                ):
                    raise PayrollRegulatoryReportError(
                        "Financial group does not match "
                        "current payroll calculations"
                    )

                matching_sources.append(source)

        if not matching_sources:
            raise PayrollRegulatoryReportError(
                "Missing employee payroll calculations"
            )

        numbers = {
            source.get("tax_number")
            for source in matching_sources
        }

        if len(numbers) != 1:
            raise PayrollRegulatoryReportError(
                "Conflicting historical taxpayer identifiers"
            )

        expected_number = next(iter(numbers))

        if not isinstance(expected_number, str):
            raise PayrollRegulatoryReportError(
                "Missing payroll taxpayer identifier"
            )

        # Review validity is checked at BOTH ends of each
        # relevant month, preventing use of an expired review.
        verified_rows = []

        for group in employee_groups:
            first, last = _report_month_bounds(
                group["income_month"]
            )

            for effective_on in (first, last):
                row = await require_reviewed_identity(
                    db,
                    company_id=company_id,
                    employee_id=employee_id,
                    review_id=review_id,
                    effective_on=effective_on,
                    expected_tax_number=expected_number,
                )
                verified_rows.append(row)

        review = verified_rows[0]

        if (
            review.review_status != "verified"
            or review.reviewed_by is None
            or review.reviewed_at is None
            or review.created_by == review.reviewed_by
        ):
            raise PayrollRegulatoryReportError(
                "Independent persisted approval required"
            )

        evidence.append({
            "company_id": company_id,
            "employee_id": employee_id,
            "identity_kind": review.identity_kind,
            "tax_number": review.tax_number,
            "evidence_reference": review.evidence_reference,
            "reviewed_by": review.reviewed_by,
            "reviewed_on": review.reviewed_at.date(),
        })

    result = attach_reviewed_4df_taxpayer_identities(
        company_id=company_id,
        financial_groups=financial_groups,
        calculation_sources=calculation_sources,
        reviewed_identities=evidence,
    )

    for item in result:
        employee_id = item["employee_id"]
        item["taxpayer_identity"]["persisted_review_id"] = (
            review_ids_by_employee[employee_id]
        )
        item["taxpayer_identity"]["persistence"] = (
            "VERIFIED_POSTGRESQL_RECORD"
        )
        item["statutory_approved"] = False

    return result
