"""Internal 4DF financial grouping for later statutory projection.

No taxpayer identifiers or legally approved income codes are
inferred. Output is REVIEW_ONLY and cannot authorize filing.
"""
import hashlib
import json
from collections import defaultdict
from decimal import Decimal

from app.services.payroll_regulatory_4df_form_contract import (
    validate_4df_financial_contract,
)
from app.services.payroll_regulatory_form_row_writer import money
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)
from app.services.payroll_regulatory_source_snapshot_service import (
    canonical_json,
)

ZERO = Decimal("0.00")

FIELDS = (
    "gross_accrued_amount",
    "pit_accrued_amount",
    "levy_accrued_amount",
    "paid_amount",
    "pit_remitted",
    "levy_remitted",
)


def project_4df_financial_groups(
    *,
    company_id,
    payroll_period_id,
    rows,
    expected_calculation_ids=None,
):
    """Aggregate reviewed financial evidence by employee/category/month.

    The statutory filing representation is intentionally NOT built
    here. In particular, a taxpayer's statutory identifier,
    legal income classification and correction markers must be
    established independently.
    """
    validate_4df_financial_contract(
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        rows=rows,
        expected_calculation_ids=expected_calculation_ids,
    )

    grouped = defaultdict(list)
    source_keys = {}

    for row in rows:
        payload = json.loads(row.payload_json)

        if (
            type(row.employee_id) is not int
            or row.employee_id <= 0
            or not isinstance(payload.get("category_code"), str)
            or not payload["category_code"].strip()
        ):
            raise PayrollRegulatoryReportError(
                "Verified employee and reviewed income category required"
            )

        key = (
            row.employee_id,
            payload["category_code"].strip(),
            payload["income_month"],
        )

        source_id = row.payroll_calculation_id
        previous = source_keys.setdefault(source_id, key)

        if previous != key:
            raise PayrollRegulatoryReportError(
                "Payroll calculation split across incompatible "
                "employee, income category or income month"
            )

        grouped[key].append((row, payload))

    result = []

    for key in sorted(grouped):
        employee_id, category, income_month = key
        entries = grouped[key]

        totals = {
            field: sum(
                (
                    money(payload[field], field)
                    for _, payload in entries
                ),
                ZERO,
            )
            for field in FIELDS
        }

        source_ids = sorted({
            row.payroll_calculation_id
            for row, _ in entries
        })

        payment_dates = sorted({
            payload["payment_date"]
            for _, payload in entries
        })

        evidence = sorted(
            (
                {
                    "source_id": row.payroll_calculation_id,
                    "source_sha256": payload["source_sha256"],
                    "slice_number": payload["slice_number"],
                    "slice_count": payload["slice_count"],
                    "payment_date": payload["payment_date"],
                    "payment_evidence_reference":
                        payload["payment_evidence_reference"],
                    "tax_remittance_evidence_reference":
                        payload["tax_remittance_evidence_reference"],
                }
                for row, payload in entries
            ),
            key=lambda item: (
                item["source_id"],
                item["slice_number"],
            ),
        )

        fingerprint = hashlib.sha256(
            canonical_json({
                "schema": "4DF_FINANCIAL_GROUP_V1",
                "company_id": company_id,
                "payroll_period_id": payroll_period_id,
                "employee_id": employee_id,
                "category": category,
                "income_month": income_month,
                "totals": {
                    field: str(value)
                    for field, value in totals.items()
                },
                "evidence": evidence,
            }).encode("utf-8")
        ).hexdigest()

        result.append({
            "status": "REVIEW_ONLY_NOT_FOR_FILING",
            "company_id": company_id,
            "payroll_period_id": payroll_period_id,
            "employee_id": employee_id,
            "reviewed_category_code": category,
            "income_month": income_month,
            "payroll_calculation_ids": source_ids,
            "payment_dates": payment_dates,
            "financial_evidence": evidence,
            "gross_accrued": str(
                totals["gross_accrued_amount"]
            ),
            "pit_accrued": str(
                totals["pit_accrued_amount"]
            ),
            "levy_accrued": str(
                totals["levy_accrued_amount"]
            ),
            "paid_amount": str(
                totals["paid_amount"]
            ),
            "pit_remitted": str(
                totals["pit_remitted"]
            ),
            "levy_remitted": str(
                totals["levy_remitted"]
            ),
            "source_fingerprint": fingerprint,
            "statutory_approved": False,
        })

    return result
