"""Verified source binding for internal 4DF financial review.

No statutory field classification, legal income code, filing permission,
or normative approval is inferred by this module.
"""

import hashlib
import re
from collections import defaultdict
from datetime import date

from app.services.payroll_regulatory_4df_integrity import (
    verify_4df_financial_projection,
)
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)
from app.services.payroll_regulatory_source_snapshot_service import (
    canonical_json,
)


STATUS = "REVIEW_ONLY_NOT_FOR_FILING"
SHA256 = re.compile(r"[0-9a-f]{64}")


def fail(message):
    raise PayrollRegulatoryReportError(message)


def positive_id(value, field):
    if type(value) is not int or value <= 0:
        fail("Invalid " + field)
    return value


def valid_digest(value, field):
    if (
        not isinstance(value, str)
        or SHA256.fullmatch(value) is None
    ):
        fail("Invalid " + field)
    return value


def valid_date(value, field):
    if not isinstance(value, str):
        fail("Invalid " + field)

    try:
        result = date.fromisoformat(value)
    except ValueError as exc:
        raise PayrollRegulatoryReportError(
            "Invalid " + field
        ) from exc

    if result.isoformat() != value:
        fail("Noncanonical " + field)

    return result


def bind_4df_financial_sources(
    *,
    company_id,
    payroll_period_id,
    financial_groups,
    calculation_sources,
):
    """Bind every dated evidence slice to current payroll sources.

    The input groups must already have passed the financial projection
    contract. Source coverage and slice completeness are checked again.

    Returns an immutable-by-convention review manifest. It is not a
    filing payload, and it never changes the provided objects.
    """

    positive_id(company_id, "company_id")
    positive_id(payroll_period_id, "payroll_period_id")

    if (
        not isinstance(calculation_sources, (list, tuple))
        or not calculation_sources
    ):
        fail("Payroll calculation sources required")

    sources = {}

    for source in calculation_sources:
        if not isinstance(source, dict):
            fail("Invalid payroll source")

        source_id = positive_id(
            source.get("payroll_calculation_id"),
            "payroll_calculation_id",
        )

        if source_id in sources:
            fail("Duplicate current payroll calculation")

        if source.get("company_id") != company_id:
            fail("Cross-company payroll source")

        if source.get("payroll_period_id") != payroll_period_id:
            fail("Cross-period payroll source")

        positive_id(source.get("employee_id"), "employee_id")

        sources[source_id] = source

    financial_manifest = verify_4df_financial_projection(
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        financial_groups=financial_groups,
        expected_calculation_ids=sorted(sources),
    )

    bindings = []
    seen_slices = set()
    seen_sources = set()

    source_slice_numbers = defaultdict(set)
    source_slice_counts = {}
    source_hashes = {}

    for group in financial_groups:
        if group.get("status") != STATUS:
            fail("Unapproved financial group")

        employee_id = group["employee_id"]
        group_source_ids = set(
            group["payroll_calculation_ids"]
        )

        evidence = group.get("financial_evidence")

        if not isinstance(evidence, list) or not evidence:
            fail("Dated payment evidence required")

        evidence_sources = set()
        evidence_dates = set()

        for item in evidence:
            if not isinstance(item, dict):
                fail("Invalid dated evidence")

            source_id = positive_id(
                item.get("source_id"),
                "evidence source ID",
            )

            if source_id not in group_source_ids:
                fail("Evidence source outside financial group")

            source = sources[source_id]

            if source["employee_id"] != employee_id:
                fail("Payroll employee identity mismatch")

            source_hash = valid_digest(
                item.get("source_sha256"),
                "source fingerprint",
            )

            prior_hash = source_hashes.setdefault(
                source_id, source_hash
            )

            if prior_hash != source_hash:
                fail("Conflicting source fingerprints")

            slice_number = positive_id(
                item.get("slice_number"),
                "slice number",
            )

            slice_count = positive_id(
                item.get("slice_count"),
                "slice count",
            )

            if slice_number > slice_count:
                fail("Invalid dated evidence slice number")

            previous_count = source_slice_counts.setdefault(
                source_id, slice_count
            )

            if previous_count != slice_count:
                fail("Conflicting slice counts")

            slice_key = (source_id, slice_number)

            if slice_key in seen_slices:
                fail("Duplicate dated evidence slice")

            seen_slices.add(slice_key)
            source_slice_numbers[source_id].add(slice_number)

            payment_date = item.get("payment_date")
            valid_date(payment_date, "payment_date")

            for field in (
                "payment_evidence_reference",
                "tax_remittance_evidence_reference",
            ):
                value = item.get(field)

                if (
                    not isinstance(value, str)
                    or not value.strip()
                ):
                    fail("Missing " + field)

            evidence_sources.add(source_id)
            evidence_dates.add(payment_date)

            bindings.append({
                "payroll_calculation_id": source_id,
                "employee_id": employee_id,
                "income_month": group["income_month"],
                "reviewed_category_code":
                    group["reviewed_category_code"],
                "slice_number": slice_number,
                "slice_count": slice_count,
                "source_sha256": source_hash,
                "payment_date": payment_date,
                "payment_evidence_reference":
                    item["payment_evidence_reference"],
                "tax_remittance_evidence_reference":
                    item["tax_remittance_evidence_reference"],
                "financial_group_fingerprint":
                    group["source_fingerprint"],
            })

        if evidence_sources != group_source_ids:
            fail("Incomplete financial evidence coverage")

        expected_dates = group.get("payment_dates")

        if (
            not isinstance(expected_dates, list)
            or len(expected_dates) != len(set(expected_dates))
            or set(expected_dates) != evidence_dates
        ):
            fail("Financial payment date mismatch")

        seen_sources.update(evidence_sources)

    if seen_sources != set(sources):
        fail("Incomplete current payroll source binding")

    for source_id in sorted(sources):
        count = source_slice_counts.get(source_id)

        if count is None:
            fail("Missing financial slice count")

        actual = source_slice_numbers[source_id]
        expected = set(range(1, count + 1))

        if actual != expected:
            fail("Incomplete dated financial slices")

    bindings.sort(
        key=lambda item: (
            item["payroll_calculation_id"],
            item["slice_number"],
        )
    )

    result = {
        "schema": "4DF_SOURCE_BINDING_V1",
        "status": STATUS,
        "statutory_approved": False,
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "financial_manifest_sha256":
            financial_manifest["manifest_sha256"],
        "source_count": len(sources),
        "slice_count": len(bindings),
        "bindings": bindings,
    }

    result["binding_sha256"] = hashlib.sha256(
        canonical_json(result).encode("utf-8")
    ).hexdigest()

    return result
