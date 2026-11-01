"""Unified internal review of payroll regulatory source evidence.

D1/4DF consume existing regulatory readiness services.
D5/D6 consume the verified HR review and immutable snapshots.

This service is read-only. It does not approve statutory mappings,
create official report rows, or submit declarations.
"""

from app.services.payroll_regulatory_readiness_service import (
    payroll_regulatory_readiness,
)

from app.services.payroll_regulatory_d5_d6_workflow import (
    build_verified_d5_d6_workflow,
    verify_review_snapshot,
)

from app.services.payroll_regulatory_hr_snapshot_store import (
    load_hr_snapshot,
    HRReviewSnapshotError,
)


class UnifiedRegulatoryReviewError(ValueError):
    pass


def require_identifier(value, field):
    if type(value) is not int or value <= 0:
        raise UnifiedRegulatoryReviewError(
            "Invalid identifier: " + field
        )
    return value


def validate_hr_snapshot_identity(
    saved,
    *,
    company_id,
    payroll_period_id,
    report_kind,
):
    if not isinstance(saved, dict):
        raise UnifiedRegulatoryReviewError(
            "Persisted HR snapshot required"
        )

    payload = saved.get("payload")

    if not isinstance(payload, dict):
        raise UnifiedRegulatoryReviewError(
            "HR snapshot payload missing"
        )

    identity = payload.get("identity")

    if not isinstance(identity, dict):
        raise UnifiedRegulatoryReviewError(
            "HR snapshot identity missing"
        )

    expected = {
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "report_kind": report_kind,
    }

    for key, expected_value in expected.items():
        if identity.get(key) != expected_value:
            raise UnifiedRegulatoryReviewError(
                "HR snapshot scope mismatch: " + key
            )

    review = payload.get("review")

    if not isinstance(review, dict):
        raise UnifiedRegulatoryReviewError(
            "HR review missing"
        )

    verify_review_snapshot(review)

    if review.get("company_id") != company_id:
        raise UnifiedRegulatoryReviewError(
            "HR review company mismatch"
        )

    if review.get("payroll_period_id") != payroll_period_id:
        raise UnifiedRegulatoryReviewError(
            "HR review period mismatch"
        )

    if (
        payload.get("official_statutory_generation")
        != "BLOCKED"
    ):
        raise UnifiedRegulatoryReviewError(
            "Unapproved HR official generation"
        )

    return True


def validate_unified_review(result):
    if not isinstance(result, dict):
        raise UnifiedRegulatoryReviewError(
            "Unified review object required"
        )

    company_id = require_identifier(
        result.get("company_id"),
        "company",
    )

    period_id = require_identifier(
        result.get("payroll_period_id"),
        "period",
    )

    reports = result.get("reports")

    if not isinstance(reports, dict):
        raise UnifiedRegulatoryReviewError(
            "Regulatory review sections missing"
        )

    if set(reports) != {"d1", "4df", "d5", "d6"}:
        raise UnifiedRegulatoryReviewError(
            "Incomplete regulatory report coverage"
        )

    for kind, section in reports.items():
        if not isinstance(section, dict):
            raise UnifiedRegulatoryReviewError(
                "Invalid report section"
            )

        if section.get("report_kind") != kind:
            raise UnifiedRegulatoryReviewError(
                "Report section identity mismatch"
            )

        if section.get("company_id") != company_id:
            raise UnifiedRegulatoryReviewError(
                "Cross-company report section"
            )

        if section.get("payroll_period_id") != period_id:
            raise UnifiedRegulatoryReviewError(
                "Cross-period report section"
            )

        if section.get("review_only") is not True:
            raise UnifiedRegulatoryReviewError(
                "Report section must remain review-only"
            )

        if section.get("official_generation") != "BLOCKED":
            raise UnifiedRegulatoryReviewError(
                "Unapproved official report generation"
            )

    if result.get("review_only") is not True:
        raise UnifiedRegulatoryReviewError(
            "Unified review-only guard missing"
        )

    if result.get("official_generation") != "BLOCKED":
        raise UnifiedRegulatoryReviewError(
            "Unified official generation must remain blocked"
        )

    return True


async def build_unified_regulatory_review(
    db,
    *,
    company_id,
    payroll_period_id,
    d5_snapshot_id=None,
    d6_snapshot_id=None,
):
    """Aggregate existing trusted services without mutating sources."""

    company_id = require_identifier(
        company_id,
        "company",
    )

    payroll_period_id = require_identifier(
        payroll_period_id,
        "period",
    )

    for kind, snapshot_id in (
        ("d5", d5_snapshot_id),
        ("d6", d6_snapshot_id),
    ):
        if snapshot_id is not None:
            require_identifier(
                snapshot_id,
                kind + " snapshot",
            )

    reports = {}

    # Existing payroll readiness retains responsibility for checking
    # employee, calculation, statutory and financial source coverage.
    for kind in ("d1", "4df"):
        readiness = await payroll_regulatory_readiness(
            db,
            company_id=company_id,
            payroll_period_id=payroll_period_id,
            report_kind=kind,
        )

        if not isinstance(readiness, dict):
            raise UnifiedRegulatoryReviewError(
                "Invalid regulatory readiness result: " + kind
            )

        reports[kind] = {
            "report_kind": kind,
            "company_id": company_id,
            "payroll_period_id": payroll_period_id,
            "review_only": True,
            "source_readiness": readiness,
            "official_generation": "BLOCKED",
        }

    # HR evidence is independently verified through the D5/D6
    # source-control workflow.
    hr_review = await build_verified_d5_d6_workflow(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
    )

    verify_review_snapshot(hr_review)

    if (
        hr_review.get("company_id") != company_id
        or hr_review.get("payroll_period_id")
        != payroll_period_id
    ):
        raise UnifiedRegulatoryReviewError(
            "HR review scope mismatch"
        )

    for kind, snapshot_id in (
        ("d5", d5_snapshot_id),
        ("d6", d6_snapshot_id),
    ):
        saved = None

        if snapshot_id is not None:
            saved = await load_hr_snapshot(
                db,
                company_id=company_id,
                snapshot_id=snapshot_id,
            )

            validate_hr_snapshot_identity(
                saved,
                company_id=company_id,
                payroll_period_id=payroll_period_id,
                report_kind=kind,
            )

        reports[kind] = {
            "report_kind": kind,
            "company_id": company_id,
            "payroll_period_id": payroll_period_id,
            "review_only": True,
            "hr_source_snapshot_sha256": (
                hr_review["snapshot_sha256"]
            ),
            "persisted_snapshot_id": (
                saved["id"] if saved else None
            ),
            "persisted_snapshot_sha256": (
                saved["sha256"] if saved else None
            ),
            "statutory_mapping": "NOT_APPROVED",
            "applicability": (
                "NOT_DETERMINED"
                if kind == "d6"
                else "REQUIRES_APPROVED_CLASSIFICATION"
            ),
            "official_generation": "BLOCKED",
        }

    result = {
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "review_only": True,
        "reports": reports,
        "official_generation": "BLOCKED",
        "overall_status": "INTERNAL_REVIEW",
    }

    validate_unified_review(result)

    return result
