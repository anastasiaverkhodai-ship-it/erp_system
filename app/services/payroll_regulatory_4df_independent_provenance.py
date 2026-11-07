"""Independent 4DF source fingerprint verification.

Source hashes are recalculated from independently reloaded
PostgreSQL payroll evidence, using the original source schema.

This service never authorizes statutory generation.
"""

import hashlib
import hmac
from decimal import Decimal

from app.services.payroll_regulatory_4df_combined_source_binding import (
    build_4df_combined_source_binding,
)
from app.services.payroll_regulatory_readiness_service import (
    payroll_regulatory_readiness,
)
from app.services.payroll_regulatory_persisted_source_verifier import (
    verify_persisted_payroll_sources,
)
from app.services.payroll_regulatory_source_snapshot_service import (
    canonical_json,
)
from app.services.payroll_regulatory_payment_evidence import (
    verify_4df_posted_payment_evidence,
)


class FourDFProvenanceError(ValueError):
    pass


def positive(value, field):
    if type(value) is not int or value <= 0:
        raise FourDFProvenanceError("Invalid " + field)
    return value


def digest(value):
    return hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()


def source_fingerprint(
    *,
    company_id,
    payroll_period_id,
    source,
):
    """Use the exact SOURCE_EVIDENCE_V1 hashing contract."""

    positive(company_id, "company_id")
    positive(payroll_period_id, "payroll_period_id")

    if not isinstance(source, dict):
        raise FourDFProvenanceError(
            "Payroll source dictionary required"
        )

    positive(
        source.get("payroll_calculation_id"),
        "payroll_calculation_id",
    )

    record = {
        "schema": "SOURCE_EVIDENCE_V1",
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "report_kind": "4df",
        "source": source,
    }

    return digest(record)


def group_fingerprint(
    *,
    company_id,
    payroll_period_id,
    group,
):
    """Rebuild the existing financial group fingerprint."""

    if not isinstance(group, dict):
        raise FourDFProvenanceError(
            "Financial group required"
        )

    for field in (
        "employee_id",
        "reviewed_category_code",
        "income_month",
        "financial_evidence",
        "gross_accrued",
        "pit_accrued",
        "levy_accrued",
        "paid_amount",
        "pit_remitted",
        "levy_remitted",
    ):
        if field not in group:
            raise FourDFProvenanceError(
                "Missing group field: " + field
            )

    evidence = group["financial_evidence"]

    if not isinstance(evidence, list) or not evidence:
        raise FourDFProvenanceError(
            "Dated financial evidence required"
        )

    for item in evidence:
        if not isinstance(item, dict):
            raise FourDFProvenanceError(
                "Invalid dated financial evidence"
            )

    fields = (
        ("gross_accrued_amount", "gross_accrued"),
        ("pit_accrued_amount", "pit_accrued"),
        ("levy_accrued_amount", "levy_accrued"),
        ("paid_amount", "paid_amount"),
        ("pit_remitted", "pit_remitted"),
        ("levy_remitted", "levy_remitted"),
    )

    body = {
        "schema": "4DF_FINANCIAL_GROUP_V1",
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "employee_id": group["employee_id"],
        "category": group["reviewed_category_code"],
        "income_month": group["income_month"],
        "totals": {
            key: str(group[value])
            for key, value in fields
        },
        "evidence": evidence,
    }

    return digest(body)


def verify_group_fingerprints(
    *,
    company_id,
    payroll_period_id,
    groups,
    calculation_sources,
):
    """Verify every slice against independently reloaded payroll.

    No self-supplied hash is accepted as evidence.
    """

    positive(company_id, "company_id")
    positive(payroll_period_id, "payroll_period_id")

    if not isinstance(groups, (list, tuple)) or not groups:
        raise FourDFProvenanceError(
            "Nonempty financial groups required"
        )

    if (
        not isinstance(calculation_sources, list)
        or not calculation_sources
    ):
        raise FourDFProvenanceError(
            "Current payroll sources required"
        )

    sources = {}

    for source in calculation_sources:
        if not isinstance(source, dict):
            raise FourDFProvenanceError(
                "Invalid payroll source"
            )

        identifier = positive(
            source.get("payroll_calculation_id"),
            "calculation",
        )

        if identifier in sources:
            raise FourDFProvenanceError(
                "Duplicate payroll source"
            )

        sources[identifier] = source

    expected_hashes = {
        identifier: source_fingerprint(
            company_id=company_id,
            payroll_period_id=payroll_period_id,
            source=source,
        )
        for identifier, source in sources.items()
    }

    seen = set()
    slices = 0

    for group in groups:
        expected_group = group_fingerprint(
            company_id=company_id,
            payroll_period_id=payroll_period_id,
            group=group,
        )

        supplied_group = group.get("source_fingerprint")

        if (
            not isinstance(supplied_group, str)
            or not hmac.compare_digest(
                supplied_group, expected_group
            )
        ):
            raise FourDFProvenanceError(
                "Financial group fingerprint mismatch"
            )

        evidence_ids = set()

        for item in group["financial_evidence"]:
            identifier = positive(
                item.get("source_id"),
                "dated source",
            )

            if identifier not in sources:
                raise FourDFProvenanceError(
                    "Unknown dated payroll source"
                )

            if item.get("source_sha256") != (
                expected_hashes[identifier]
            ):
                raise FourDFProvenanceError(
                    "Independent source fingerprint mismatch"
                )

            if group.get("employee_id") != (
                sources[identifier]["employee_id"]
            ):
                raise FourDFProvenanceError(
                    "Cross-employee source"
                )

            evidence_ids.add(identifier)
            slices += 1

        group_ids = group.get("payroll_calculation_ids")

        if (
            not isinstance(group_ids, list)
            or set(group_ids) != evidence_ids
        ):
            raise FourDFProvenanceError(
                "Financial group source coverage mismatch"
            )

        if seen.intersection(group_ids):
            raise FourDFProvenanceError(
                "Payroll source reused across groups"
            )

        seen.update(group_ids)

    if seen != set(sources):
        raise FourDFProvenanceError(
            "Incomplete financial source coverage"
        )

    return {
        "status": "SOURCE_FINGERPRINT_VERIFIED",
        "source_count": len(sources),
        "slice_count": slices,
        "source_fingerprints": [
            {
                "payroll_calculation_id": identifier,
                "source_sha256": expected_hashes[identifier],
            }
            for identifier in sorted(sources)
        ],
        "official_generation": "BLOCKED",
    }


async def build_independent_4df_provenance(
    db,
    *,
    company_id,
    payroll_period_id,
    financial_groups,
    reviewed_allocations=None,
):
    """Verify source hashes and optionally real posted payments."""

    positive(company_id, "company_id")
    positive(payroll_period_id, "payroll_period_id")

    if not isinstance(financial_groups, (list, tuple)):
        raise FourDFProvenanceError(
            "Financial groups required"
        )

    if not financial_groups:
        raise FourDFProvenanceError(
            "Empty financial groups cannot be verified"
        )

    # Existing D1/4DF/D6 review validates source totals,
    # dated slice topology and source coverage first.
    combined = await build_4df_combined_source_binding(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        financial_groups=financial_groups,
    )

    if combined["body"]["four_df_status"] != (
        "SLICE_TOPOLOGY_VERIFIED"
    ):
        raise FourDFProvenanceError(
            "4DF topology must be verified first"
        )

    readiness = await payroll_regulatory_readiness(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        report_kind="4df",
    )

    persisted = await verify_persisted_payroll_sources(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        readiness=readiness,
    )

    if persisted.get("verified") is not True:
        raise FourDFProvenanceError(
            "Independent persisted verification missing"
        )

    verified = verify_group_fingerprints(
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        groups=financial_groups,
        calculation_sources=readiness["calculation_sources"],
    )

    posted = False

    if reviewed_allocations is not None:
        if (
            not isinstance(reviewed_allocations, list)
            or not reviewed_allocations
        ):
            raise FourDFProvenanceError(
                "Posted payment allocations required"
            )

        from copy import deepcopy
        from datetime import date

        verified_allocations = deepcopy(reviewed_allocations)
        current = {
            row["payroll_calculation_id"]: row
            for row in readiness["calculation_sources"]
        }

        evidence_items = {}

        for group in financial_groups:
            for item in group["financial_evidence"]:
                key = (
                    item["source_id"],
                    item["slice_number"],
                )
                if key in evidence_items:
                    raise FourDFProvenanceError(
                        "Duplicate bound payment slice"
                    )
                evidence_items[key] = item

        seen = set()

        for allocation in verified_allocations:
            if not isinstance(allocation, dict):
                raise FourDFProvenanceError(
                    "Invalid payment allocation"
                )

            calc = positive(
                allocation.get("payroll_calculation_id"),
                "payment calculation",
            )
            if calc not in current:
                raise FourDFProvenanceError(
                    "Payment from unknown payroll source"
                )

            number = positive(
                allocation.get("slice_number"),
                "slice number",
            )
            key = (calc, number)

            if key in seen or key not in evidence_items:
                raise FourDFProvenanceError(
                    "Duplicate or unexpected payment slice"
                )

            seen.add(key)
            expected = evidence_items[key]

            when = allocation.get("payment_date")
            if type(when) is not date:
                raise FourDFProvenanceError(
                    "Real payment date object required"
                )

            if expected.get("payment_date") != when.isoformat():
                raise FourDFProvenanceError(
                    "Payment date differs from dated slice"
                )

            for field in (
                "payment_evidence_reference",
                "tax_remittance_evidence_reference",
            ):
                if allocation.get(field) != expected.get(field):
                    raise FourDFProvenanceError(
                        "Payment reference differs from dated slice"
                    )

            allocation["_verified_employee_id"] = (
                current[calc]["employee_id"]
            )

        if seen != set(evidence_items):
            raise FourDFProvenanceError(
                "Missing actual posted payment slices"
            )

        # Existing PostgreSQL-backed verifier checks actual
        # posted journals, dates, IDs and allocated tax amounts.
        await verify_4df_posted_payment_evidence(
            db,
            company_id=company_id,
            reviewed_allocations=verified_allocations,
        )
        posted = True

    result = {
        "schema": "4DF_INDEPENDENT_PROVENANCE_V1",
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "combined_review_sha256": combined["review_sha256"],
        "source_count": verified["source_count"],
        "slice_count": verified["slice_count"],
        "source_fingerprints": verified["source_fingerprints"],
        "source_fingerprint_verified": True,
        "posted_payment_verified": posted,
        "gross_paid_income_verified": False,
        "normative_mapping_approved": False,
        "review_only": True,
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }

    result["review_sha256"] = digest(result)
    return result
