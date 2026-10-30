"""Fail-closed integrity verification for internal 4DF groups.

This is financial review functionality, not an official statutory
form projection. It cannot approve mappings or authorize filing.
"""

import hashlib
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)
from app.services.payroll_regulatory_source_snapshot_service import (
    canonical_json,
)


FIELDS = (
    "gross_accrued",
    "pit_accrued",
    "levy_accrued",
    "paid_amount",
    "pit_remitted",
    "levy_remitted",
)

DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _fail(message):
    raise PayrollRegulatoryReportError(message)


def _money(value, field):
    if isinstance(value, (bool, float)):
        _fail(f"Invalid 4DF {field}")

    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        _fail(f"Invalid 4DF {field}")

    if (
        not number.is_finite()
        or number < 0
        or number.as_tuple().exponent < -2
    ):
        _fail(f"Invalid 4DF {field}")

    return number.quantize(Decimal("0.01"))


def _positive_id(value, field):
    if type(value) is not int or value <= 0:
        _fail(f"Invalid {field}")
    return value


def verify_4df_financial_projection(
    *,
    company_id,
    payroll_period_id,
    financial_groups,
    expected_calculation_ids,
):
    """Reconcile all internal financial groups against source IDs.

    Rejects duplicate sources, omitted sources, cross-company data,
    invalid reporting months, malformed fingerprints and invalid
    financial values. Results remain REVIEW_ONLY.
    """

    _positive_id(company_id, "company_id")
    _positive_id(payroll_period_id, "payroll_period_id")

    if not isinstance(financial_groups, (list, tuple)):
        _fail("Financial groups must be a sequence")

    if not financial_groups:
        _fail("Empty 4DF projection")

    if not isinstance(expected_calculation_ids, (list, tuple, set)):
        _fail("Expected payroll sources required")

    expected = list(expected_calculation_ids)

    for value in expected:
        _positive_id(value, "payroll calculation ID")

    if not expected or len(expected) != len(set(expected)):
        _fail("Invalid expected payroll source set")

    encountered = set()
    group_keys = set()
    monthly = defaultdict(
        lambda: {
            field: Decimal("0.00")
            for field in FIELDS
        }
    )

    for group in financial_groups:
        if not isinstance(group, dict):
            _fail("Invalid 4DF financial group")

        if (
            group.get("status") != "REVIEW_ONLY_NOT_FOR_FILING"
            or group.get("statutory_approved") is not False
        ):
            _fail("Unexpected statutory authorization")

        if (
            group.get("company_id") != company_id
            or group.get("payroll_period_id") != payroll_period_id
        ):
            _fail("Cross-company or cross-period 4DF group")

        employee_id = _positive_id(
            group.get("employee_id"),
            "employee_id",
        )

        category = group.get("reviewed_category_code")

        if not isinstance(category, str) or not category.strip():
            _fail("Reviewed income category required")

        month = group.get("income_month")

        if not isinstance(month, str):
            _fail("Invalid income month")

        try:
            parsed_month = date.fromisoformat(month)
        except ValueError:
            _fail("Invalid income month")

        if (
            parsed_month.day != 1
            or parsed_month.isoformat() != month
        ):
            _fail("Noncanonical income month")

        key = (employee_id, category.strip(), month)

        if key in group_keys:
            _fail("Duplicate employee/category/month group")

        group_keys.add(key)

        fingerprint = group.get("source_fingerprint")

        if (
            not isinstance(fingerprint, str)
            or DIGEST.fullmatch(fingerprint) is None
        ):
            _fail("Invalid 4DF source fingerprint")

        sources = group.get("payroll_calculation_ids")

        if not isinstance(sources, list) or not sources:
            _fail("Missing financial group sources")

        if len(sources) != len(set(sources)):
            _fail("Duplicate source within group")

        for source_id in sources:
            _positive_id(source_id, "source_id")

            if source_id in encountered:
                _fail("Payroll source counted in multiple groups")

            encountered.add(source_id)

        for field in FIELDS:
            monthly[month][field] += _money(
                group.get(field),
                field,
            )

        if _money(
            group.get("pit_remitted"),
            "pit_remitted",
        ) > _money(
            group.get("pit_accrued"),
            "pit_accrued",
        ):
            _fail("Remitted PIT exceeds accrued PIT")

        if _money(
            group.get("levy_remitted"),
            "levy_remitted",
        ) > _money(
            group.get("levy_accrued"),
            "levy_accrued",
        ):
            _fail("Remitted levy exceeds accrued levy")

    if encountered != set(expected):
        _fail("Incomplete or unexpected payroll source coverage")

    months = []

    for month in sorted(monthly):
        months.append({
            "income_month": month,
            "totals": {
                field: str(monthly[month][field])
                for field in FIELDS
            },
        })

    sources = sorted(encountered)

    manifest = {
        "schema": "4DF_FINANCIAL_INTEGRITY_V1",
        "status": "REVIEW_ONLY_NOT_FOR_FILING",
        "statutory_approved": False,
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "source_ids": sources,
        "source_count": len(sources),
        "group_count": len(financial_groups),
        "monthly_totals": months,
        "group_fingerprints": sorted(
            group["source_fingerprint"]
            for group in financial_groups
        ),
    }

    manifest["manifest_sha256"] = hashlib.sha256(
        canonical_json(manifest).encode("utf-8")
    ).hexdigest()

    return manifest
