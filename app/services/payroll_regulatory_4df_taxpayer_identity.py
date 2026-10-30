"""Verified identity linkage for internal 4DF financial groups.

This does not determine whether an identifier is legally valid
for statutory filing. It does not support passport exceptions
without a separate, approved identity-classification policy.
"""
from copy import deepcopy
from datetime import date
import re

from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)

RNOКPP_PATTERN = re.compile(r"^[0-9]{10}$")


def attach_reviewed_4df_taxpayer_identities(
    *,
    company_id,
    financial_groups,
    calculation_sources,
    reviewed_identities,
):
    """Attach independently reviewed RNOКPP evidence.

    The taxpayer number must agree with current payroll readiness.
    Identity evidence must be explicit and employee-scoped.
    No automatic statutory approval is performed.
    """
    if not isinstance(financial_groups, (list, tuple)):
        raise PayrollRegulatoryReportError(
            "Financial groups required"
        )

    if not financial_groups:
        raise PayrollRegulatoryReportError(
            "Empty 4DF financial projection"
        )

    if not isinstance(calculation_sources, (list, tuple)):
        raise PayrollRegulatoryReportError(
            "Payroll calculation sources required"
        )

    if not isinstance(reviewed_identities, (list, tuple)):
        raise PayrollRegulatoryReportError(
            "Reviewed taxpayer identities required"
        )

    source_map = {}
    sources_by_employee = {}

    for source in calculation_sources:
        if not isinstance(source, dict):
            raise PayrollRegulatoryReportError(
                "Invalid payroll calculation source"
            )

        calculation_id = source.get("payroll_calculation_id")
        employee_id = source.get("employee_id")

        if (
            type(calculation_id) is not int
            or calculation_id <= 0
            or type(employee_id) is not int
            or employee_id <= 0
            or calculation_id in source_map
        ):
            raise PayrollRegulatoryReportError(
                "Invalid or duplicate payroll source identity"
            )

        source_map[calculation_id] = source
        sources_by_employee.setdefault(employee_id, []).append(source)

    evidence_by_employee = {}

    for evidence in reviewed_identities:
        if not isinstance(evidence, dict):
            raise PayrollRegulatoryReportError(
                "Invalid identity review evidence"
            )

        employee_id = evidence.get("employee_id")
        identifier = evidence.get("tax_number")
        reference = evidence.get("evidence_reference")
        reviewer = evidence.get("reviewed_by")
        reviewed_on = evidence.get("reviewed_on")

        if (
            type(employee_id) is not int
            or employee_id <= 0
            or employee_id in evidence_by_employee
            or evidence.get("company_id") != company_id
            or evidence.get("identity_kind") != "rnokpp"
            or not isinstance(identifier, str)
            or RNOКPP_PATTERN.fullmatch(identifier) is None
            or not isinstance(reference, str)
            or not reference.strip()
            or type(reviewer) is not int
            or reviewer <= 0
            or type(reviewed_on) is not date
            or reviewed_on > date.today()
        ):
            raise PayrollRegulatoryReportError(
                "Invalid, duplicate or unreviewed taxpayer identity"
            )

        evidence_by_employee[employee_id] = evidence

    used_employees = set()
    used_calculations = set()
    number_owner = {}
    output = []

    for original_group in financial_groups:
        if not isinstance(original_group, dict):
            raise PayrollRegulatoryReportError(
                "Invalid 4DF financial group"
            )

        group = deepcopy(original_group)
        employee_id = group.get("employee_id")
        calculations = group.get("payroll_calculation_ids")

        if (
            group.get("company_id") != company_id
            or group.get("status") != "REVIEW_ONLY_NOT_FOR_FILING"
            or group.get("statutory_approved") is not False
            or type(employee_id) is not int
            or not isinstance(calculations, list)
            or not calculations
        ):
            raise PayrollRegulatoryReportError(
                "Invalid 4DF group identity or review status"
            )

        evidence = evidence_by_employee.get(employee_id)

        if evidence is None:
            raise PayrollRegulatoryReportError(
                "Missing independently reviewed taxpayer identity"
            )

        for calculation_id in calculations:
            if (
                type(calculation_id) is not int
                or calculation_id in used_calculations
            ):
                raise PayrollRegulatoryReportError(
                    "Payroll calculation attributed multiple times"
                )

            source = source_map.get(calculation_id)

            if (
                source is None
                or source["employee_id"] != employee_id
                or source.get("tax_number") != evidence["tax_number"]
            ):
                raise PayrollRegulatoryReportError(
                    "Taxpayer identity disagrees with current payroll source"
                )

            used_calculations.add(calculation_id)

        tax_number = evidence["tax_number"]
        previous_owner = number_owner.setdefault(
            tax_number,
            employee_id,
        )

        if previous_owner != employee_id:
            raise PayrollRegulatoryReportError(
                "Taxpayer number attributed to multiple employees"
            )

        used_employees.add(employee_id)

        group["taxpayer_identity"] = {
            "identity_kind": "rnokpp",
            "tax_number": tax_number,
            "evidence_reference": evidence["evidence_reference"],
            "reviewed_by": evidence["reviewed_by"],
            "reviewed_on": evidence["reviewed_on"].isoformat(),
            "identity_source": "PAYROLL_EMPLOYEE_TAX_NUMBER",
        }

        group["status"] = "IDENTITY_REVIEWED_NOT_FOR_FILING"
        group["statutory_approved"] = False

        output.append(group)

    if (
        used_calculations != set(source_map)
        or used_employees != set(evidence_by_employee)
    ):
        raise PayrollRegulatoryReportError(
            "Taxpayer identity review does not cover "
            "exactly the current payroll sources"
        )

    return output
