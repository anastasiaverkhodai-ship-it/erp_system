"""Financially reconciled multi-slice 4DF draft allocation.

This is internal, reviewed evidence only. No official field approval,
statutory form finalization, or filing authorization is implied.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json

from app.services.payroll_regulatory_source_snapshot_service import (
    canonical_json,
)


class FourDFSplitAllocationError(ValueError):
    pass


ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def fail(message):
    raise FourDFSplitAllocationError(message)


def positive_id(value, label):
    if type(value) is not int or value <= 0:
        fail("Invalid " + label)
    return value


def amount(value, label):
    if isinstance(value, bool) or value is None:
        fail("Invalid " + label)
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        fail("Invalid " + label)
    if not result.is_finite() or result < 0:
        fail("Invalid " + label)
    if result.quantize(CENT) != result:
        fail("Invalid money precision: " + label)
    return result.quantize(CENT)


def required_date(value, label):
    try:
        result = date.fromisoformat(value) if isinstance(value, str) else value
    except ValueError:
        fail("Invalid " + label)
    if type(result) is not date:
        fail("Invalid " + label)
    return result


def required_text(value, label):
    if not isinstance(value, str) or not value.strip():
        fail("Missing " + label)
    return value.strip()


def source_taxes(source):
    totals = {
        "personal_income_tax": ZERO,
        "military_levy": ZERO,
    }
    components = source.get("components")
    if not isinstance(components, list):
        fail("Payroll source components missing")
    seen_ids = set()
    for line in components:
        if not isinstance(line, dict):
            fail("Invalid payroll source component")
        component_id = line.get("source_line_id")
        positive_id(component_id, "statutory source line")
        if component_id in seen_ids:
            fail("Duplicate statutory component line")
        seen_ids.add(component_id)
        kind = line.get("component")
        if kind in totals:
            totals[kind] += amount(line.get("amount"), kind)
    return totals


def build_split_4df_draft_rows(*, report, readiness, evidence, approvals):
    """Return reconciled, source-bound, non-official draft report row dictionaries."""
    if report.report_kind != "4df":
        fail("Only 4DF split allocation is supported")

    if not approvals or not isinstance(evidence, (list, tuple)):
        fail("Approved mapping and reviewed allocations required")

    for key in ("company_id", "payroll_period_id", "report_kind"):
        if readiness.get(key) != getattr(report, key):
            fail("Report identity mismatch")

    if "PAYROLL_PERIOD_NOT_FINALIZED" in (
        readiness.get("blocking_issues") or ()
    ):
        fail("Payroll period is not finalized")

    source_list = readiness.get("calculation_sources")
    if not isinstance(source_list, list) or not source_list:
        fail("Payroll sources missing")

    sources = {}
    for source in source_list:
        if not isinstance(source, dict):
            fail("Invalid payroll source")
        source_id = positive_id(
            source.get("payroll_calculation_id"), "payroll calculation"
        )
        if source_id in sources or source.get("issues"):
            fail("Duplicate or unresolved payroll source")
        sources[source_id] = source

    grouped = defaultdict(list)

    for item in evidence:
        if not isinstance(item, dict):
            fail("Invalid reviewed allocation")
        source_id = positive_id(
            item.get("payroll_calculation_id"), "payroll calculation"
        )
        if source_id not in sources:
            fail("Stale or unrelated payroll calculation")
        grouped[source_id].append(item)

    if set(grouped) != set(sources):
        fail("Incomplete payroll calculation coverage")

    result = []

    for source_id in sorted(sources):
        source = sources[source_id]
        allocations = grouped[source_id]
        expected_taxes = source_taxes(source)

        numbers = set()
        counts = set()
        sums = {
            "accrued_amount": ZERO,
            "pit_accrued": ZERO,
            "levy_accrued": ZERO,
        }

        for item in allocations:
            number = positive_id(item.get("slice_number"), "slice number")
            count = positive_id(item.get("slice_count"), "slice count")
            if number in numbers:
                fail("Duplicate 4DF slice number")
            numbers.add(number)
            counts.add(count)

        if len(counts) != 1:
            fail("Inconsistent 4DF slice counts")
        count = counts.pop()
        if count != len(allocations) or numbers != set(range(1, count + 1)):
            fail("Incomplete 4DF slice sequence")

        source_record = {
            "schema": "SOURCE_EVIDENCE_V1",
            "company_id": report.company_id,
            "payroll_period_id": report.payroll_period_id,
            "report_kind": "4df",
            "source": source,
        }

        source_digest = hashlib.sha256(
            canonical_json(source_record).encode("utf-8")
        ).hexdigest()

        for item in sorted(allocations, key=lambda x: x["slice_number"]):
            mapping_id = positive_id(
                item.get("normative_mapping_id"), "normative mapping"
            )
            mapping = approvals.get(mapping_id)
            if mapping is None:
                fail("Unapproved normative mapping")

            if (
                item.get("official_reference") != mapping.official_reference
                or item.get("normative_source_sha256") != mapping.source_sha256
            ):
                fail("Normative evidence mismatch")

            category = required_text(item.get("category_code"), "category")
            review = required_text(
                item.get("review_reference"), "review reference"
            )
            payment_ref = required_text(
                item.get("payment_evidence_reference"),
                "payment evidence reference",
            )
            remittance_ref = required_text(
                item.get("tax_remittance_evidence_reference"),
                "tax remittance reference",
            )
            income_month = required_date(
                item.get("income_month"), "income month"
            )
            payment_date = required_date(
                item.get("payment_date"), "payment date"
            )
            if income_month.day != 1:
                fail("Income month must start on day 1")

            accrued = amount(item.get("accrued_amount"), "accrual")
            pit = amount(item.get("pit_accrued"), "PIT")
            levy = amount(item.get("levy_accrued"), "levy")
            paid = amount(item.get("paid_amount"), "paid income")
            pit_paid = amount(item.get("pit_remitted"), "remitted PIT")
            levy_paid = amount(item.get("levy_remitted"), "remitted levy")

            if paid > accrued or pit_paid > pit or levy_paid > levy:
                fail("4DF paid amount exceeds accrued amount")

            sums["accrued_amount"] += accrued
            sums["pit_accrued"] += pit
            sums["levy_accrued"] += levy

            payload = {
                "company_id": report.company_id,
                "payroll_period_id": report.payroll_period_id,
                "report_kind": "4df",
                "source_id": source_id,
                "source_sha256": source_digest,
                "normative_mapping_id": mapping_id,
                "normative_source_sha256": mapping.source_sha256,
                "official_reference": mapping.official_reference,
                "review_reference": review,
                "category_code": category,
                "income_month": income_month.isoformat(),
                "payment_date": payment_date.isoformat(),
                "accrued_amount": str(accrued),
                "pit_accrued": str(pit),
                "levy_accrued": str(levy),
                "paid_amount": str(paid),
                "pit_remitted": str(pit_paid),
                "levy_remitted": str(levy_paid),
                "payment_evidence_reference": payment_ref,
                "tax_remittance_evidence_reference": remittance_ref,
                "slice_number": item["slice_number"],
                "slice_count": count,
                "currency_code": "UAH",
                "official_generation": "BLOCKED",
            }

            result.append({
                "company_id": report.company_id,
                "employee_id": positive_id(
                    source.get("employee_id"), "employee"
                ),
                "employment_contract_id": positive_id(
                    source.get("employment_contract_id"),
                    "employment contract",
                ),
                "payroll_calculation_id": source_id,
                "row_code": "4DF_SPLIT_REVIEWED_SOURCE_V1",
                "payload_json": canonical_json(payload),
            })

        if sums["accrued_amount"] != amount(
            source.get("gross_amount"), "source gross"
        ):
            fail("Split 4DF gross does not reconcile")

        if sums["pit_accrued"] != expected_taxes["personal_income_tax"]:
            fail("Split 4DF PIT does not reconcile")

        if sums["levy_accrued"] != expected_taxes["military_levy"]:
            fail("Split 4DF military levy does not reconcile")

    result.sort(key=lambda row: (
        row["employee_id"],
        row["employment_contract_id"],
        row["payroll_calculation_id"],
        json.loads(row["payload_json"])["slice_number"],
    ))

    return result
