"""Append-only internal D1/4DF payroll correction snapshots.

Only source-verified financial projections can be stored. This does
not authorize official form generation or statutory submission.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import text

from app.services.payroll_regulatory_correction_projection import (
    canonical_bytes,
    review_and_seal_persisted_correction,
    verify_projection_seal,
)
from app.services.payroll_regulatory_financial_reconciliation import (
    FinancialReconciliationError,
)


class CorrectionSnapshotError(FinancialReconciliationError):
    pass


SCHEMA = "payroll_internal_correction_projection_v1"


def validate_snapshot_envelope(
    *,
    company_id,
    payroll_period_id,
    report_kind,
    sealed,
):
    if type(company_id) is not int or company_id <= 0:
        raise CorrectionSnapshotError("Invalid company")

    if type(payroll_period_id) is not int or payroll_period_id <= 0:
        raise CorrectionSnapshotError("Invalid payroll period")

    if report_kind not in ("d1", "4df"):
        raise CorrectionSnapshotError("Unsupported report")

    if not verify_projection_seal(sealed):
        raise CorrectionSnapshotError("Invalid snapshot integrity")

    payload = sealed["payload"]

    if (
        payload.get("company_id") != company_id
        or payload.get("payroll_period_id") != payroll_period_id
        or payload.get("report_kind") != report_kind
        or payload.get("schema") != SCHEMA
        or payload.get("source_provenance") != "PERSISTED_POSTGRESQL"
        or payload.get("official_form_fields_verified") is not False
        or payload.get("official_generation") != "BLOCKED"
    ):
        raise CorrectionSnapshotError(
            "Invalid source-bound projection scope"
        )

    originals = payload.get(
        "excluded_historical_calculation_ids"
    )
    replacements = payload.get("active_calculation_ids")

    if (
        not isinstance(originals, list)
        or not isinstance(replacements, list)
        or len(originals) != 1
        or len(replacements) != 1
        or type(originals[0]) is not int
        or type(replacements[0]) is not int
        or originals[0] <= 0
        or replacements[0] <= 0
        or originals[0] == replacements[0]
    ):
        raise CorrectionSnapshotError(
            "Expected one real original/replacement pair"
        )

    if not isinstance(payload.get("components"), list):
        raise CorrectionSnapshotError("Missing financial components")

    digest = hashlib.sha256(canonical_bytes(payload)).hexdigest()

    if digest != sealed["digest"]:
        raise CorrectionSnapshotError("Snapshot digest mismatch")

    return {
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "report_kind": report_kind,
        "original_calculation_id": originals[0],
        "replacement_calculation_id": replacements[0],
        "schema_version": SCHEMA,
        "projection_json": canonical_bytes(payload).decode("utf-8"),
        "projection_sha256": digest,
    }


async def persist_correction_snapshot(
    db,
    *,
    company_id,
    payroll_period_id,
    report_kind,
):
    """Persist verified evidence exactly once.

    No commit occurs here. The caller owns the transaction.
    """

    result = await review_and_seal_persisted_correction(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        report_kind=report_kind,
    )

    if (
        result.get("status")
        != "INTERNAL_FINANCIAL_PROJECTION_VERIFIED"
        or result.get("official_generation") != "BLOCKED"
        or result.get("database_snapshot_persisted") is not False
    ):
        raise CorrectionSnapshotError(
            "Financial correction projection is not verified"
        )

    params = validate_snapshot_envelope(
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        report_kind=report_kind,
        sealed=result["seal"],
    )

    inserted = await db.execute(
        text("""
            INSERT INTO public.payroll_regulatory_correction_snapshots (
                company_id,
                payroll_period_id,
                report_kind,
                original_calculation_id,
                replacement_calculation_id,
                schema_version,
                projection,
                projection_sha256
            )
            VALUES (
                :company_id,
                :payroll_period_id,
                :report_kind,
                :original_calculation_id,
                :replacement_calculation_id,
                :schema_version,
                CAST(:projection_json AS jsonb),
                :projection_sha256
            )
            ON CONFLICT ON CONSTRAINT
                uq_prcs_correction_snapshot
            DO NOTHING
            RETURNING id
        """),
        params,
    )

    new_id = inserted.scalar_one_or_none()

    existing = (
        await db.execute(
            text("""
                SELECT
                    id,
                    projection,
                    projection_sha256,
                    schema_version
                FROM public.payroll_regulatory_correction_snapshots
                WHERE company_id = :company_id
                  AND payroll_period_id = :payroll_period_id
                  AND report_kind = :report_kind
                  AND original_calculation_id =
                    :original_calculation_id
                  AND replacement_calculation_id =
                    :replacement_calculation_id
            """),
            params,
        )
    ).mappings().one()

    actual_digest = hashlib.sha256(
        canonical_bytes(existing["projection"])
    ).hexdigest()

    if (
        actual_digest != params["projection_sha256"]
        or existing["projection_sha256"]
        != params["projection_sha256"]
        or existing["schema_version"] != SCHEMA
    ):
        raise CorrectionSnapshotError(
            "Conflicting immutable correction snapshot"
        )

    return {
        "status": "PERSISTED_INTERNAL_SNAPSHOT_VERIFIED",
        "id": existing["id"],
        "created": new_id is not None,
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "report_kind": report_kind,
        "projection_sha256": actual_digest,
        "database_snapshot_persisted": True,
        "owner_role_separation_verified": False,
        "official_form_fields_verified": False,
        "official_generation": "BLOCKED",
    }
