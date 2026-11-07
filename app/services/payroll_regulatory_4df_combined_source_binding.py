"""Bind internal 4DF dated slices to verified current payroll.

This is structural/provenance review, not official 4DF approval.
"""

import hashlib
import json
from copy import deepcopy

from app.services.payroll_regulatory_combined_financial_review import (
    build_combined_financial_review,
    validate_combined_review,
)
from app.services.payroll_regulatory_readiness_service import (
    payroll_regulatory_readiness,
)
from app.services.payroll_regulatory_persisted_source_verifier import (
    verify_persisted_payroll_sources,
)
from app.services.payroll_regulatory_source_reconciliation import (
    reconcile_regulatory_sources,
)
from app.services.payroll_regulatory_4df_source_binding import (
    bind_4df_financial_sources,
)


class FourDFCombinedBindingError(ValueError):
    pass


SCHEMA = "4DF_COMBINED_SOURCE_BINDING_V1"


def _positive(value, field):
    if type(value) is not int or value <= 0:
        raise FourDFCombinedBindingError("Invalid " + field)


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _guard(report):
    if type(report) is not dict:
        raise FourDFCombinedBindingError("Review required")

    if set(report) != {"body", "review_sha256"}:
        raise FourDFCombinedBindingError(
            "Unexpected review structure"
        )

    body = report["body"]

    if type(body) is not dict:
        raise FourDFCombinedBindingError(
            "Invalid review body"
        )

    required = {
        "schema",
        "company_id",
        "payroll_period_id",
        "combined_review_sha256",
        "four_df_status",
        "source_binding",
        "source_count",
        "slice_count",
        "independent_fingerprint_verification",
        "gross_paid_income_verified",
        "statutory_mapping_approved",
        "review_only",
        "official_generation",
        "official_rows_generated",
    }

    if set(body) != required:
        raise FourDFCombinedBindingError(
            "Review fields do not match schema"
        )

    _positive(body["company_id"], "company_id")
    _positive(body["payroll_period_id"], "payroll_period_id")

    if (
        body["schema"] != SCHEMA
        or body["review_only"] is not True
        or body["gross_paid_income_verified"] is not False
        or body["statutory_mapping_approved"] is not False
        or body["independent_fingerprint_verification"]
            is not False
        or body["official_generation"] != "BLOCKED"
        or type(body["official_rows_generated"]) is not int
        or body["official_rows_generated"] != 0
    ):
        raise FourDFCombinedBindingError(
            "Statutory review-only guard failed"
        )

    binding = body["source_binding"]

    if body["four_df_status"] == "NOT_VERIFIED":
        if (
            binding is not None
            or body["source_count"] != 0
            or body["slice_count"] != 0
        ):
            raise FourDFCombinedBindingError(
                "Invalid missing-source review"
            )
    elif body["four_df_status"] == "SLICE_TOPOLOGY_VERIFIED":
        if (
            type(binding) is not dict
            or binding.get("status")
                != "REVIEW_ONLY_NOT_FOR_FILING"
            or binding.get("statutory_approved") is not False
            or binding.get("company_id")
                != body["company_id"]
            or binding.get("payroll_period_id")
                != body["payroll_period_id"]
            or binding.get("source_count")
                != body["source_count"]
            or binding.get("slice_count")
                != body["slice_count"]
        ):
            raise FourDFCombinedBindingError(
                "Source binding integrity mismatch"
            )

        source_manifest = {
            key: value
            for key, value in binding.items()
            if key != "binding_sha256"
        }

        from app.services.payroll_regulatory_source_snapshot_service import (
            canonical_json,
        )

        expected = hashlib.sha256(
            canonical_json(source_manifest).encode("utf-8")
        ).hexdigest()

        if binding.get("binding_sha256") != expected:
            raise FourDFCombinedBindingError(
                "Source binding checksum mismatch"
            )
    else:
        raise FourDFCombinedBindingError(
            "Unsupported 4DF binding status"
        )

    if report["review_sha256"] != _digest(body):
        raise FourDFCombinedBindingError(
            "Review digest mismatch"
        )

    return True


async def build_4df_combined_source_binding(
    db,
    *,
    company_id,
    payroll_period_id,
    financial_groups=None,
):
    _positive(company_id, "company_id")
    _positive(payroll_period_id, "payroll_period_id")

    try:
        combined = await build_combined_financial_review(
            db,
            company_id=company_id,
            payroll_period_id=payroll_period_id,
            financial_groups=financial_groups,
        )

        validate_combined_review(combined)

        source_binding = None
        status = "NOT_VERIFIED"
        source_count = 0
        slice_count = 0

        if financial_groups is not None:
            readiness = await payroll_regulatory_readiness(
                db,
                company_id=company_id,
                payroll_period_id=payroll_period_id,
                report_kind="4df",
            )

            await verify_persisted_payroll_sources(
                db,
                company_id=company_id,
                payroll_period_id=payroll_period_id,
                readiness=readiness,
            )

            totals = reconcile_regulatory_sources(readiness)

            if (
                sorted(totals.source_ids)
                != combined["payroll"]["calculation_ids"]
            ):
                raise FourDFCombinedBindingError(
                    "D1/4DF payroll calculation coverage differs"
                )

            normalized_sources = []

            for item in readiness["calculation_sources"]:
                source = deepcopy(item)
                source["company_id"] = company_id
                source["payroll_period_id"] = payroll_period_id
                normalized_sources.append(source)

            source_binding = bind_4df_financial_sources(
                company_id=company_id,
                payroll_period_id=payroll_period_id,
                financial_groups=financial_groups,
                calculation_sources=normalized_sources,
            )

            if (
                source_binding["financial_manifest_sha256"]
                != combined["four_df"]["manifest_sha256"]
            ):
                raise FourDFCombinedBindingError(
                    "4DF financial manifest changed"
                )

            source_count = source_binding["source_count"]
            slice_count = source_binding["slice_count"]
            status = "SLICE_TOPOLOGY_VERIFIED"

    except Exception as exc:
        raise FourDFCombinedBindingError(
            "Persisted 4DF source binding rejected"
        ) from exc

    body = {
        "schema": SCHEMA,
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "combined_review_sha256": combined["review_sha256"],
        "four_df_status": status,
        "source_binding": source_binding,
        "source_count": source_count,
        "slice_count": slice_count,
        "independent_fingerprint_verification": False,
        "gross_paid_income_verified": False,
        "statutory_mapping_approved": False,
        "review_only": True,
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }

    report = {
        "body": body,
        "review_sha256": _digest(body),
    }

    _guard(report)
    return report


async def verify_4df_combined_source_binding(
    db,
    report,
    *,
    financial_groups=None,
):
    _guard(report)

    body = report["body"]

    try:
        rebuilt = await build_4df_combined_source_binding(
            db,
            company_id=body["company_id"],
            payroll_period_id=body["payroll_period_id"],
            financial_groups=financial_groups,
        )
    except Exception as exc:
        raise FourDFCombinedBindingError(
            "Live PostgreSQL source review rejected"
        ) from exc

    if rebuilt != report:
        raise FourDFCombinedBindingError(
            "4DF source binding is stale"
        )

    return True
