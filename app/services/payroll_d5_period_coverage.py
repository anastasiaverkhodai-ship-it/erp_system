"""Fail-closed D5 source coverage for an actual payroll period.

Internal source review only. No statutory generation.
"""

from sqlalchemy import select

from app.models.payroll import PayrollPeriod

from app.services.payroll_d5_event_sources import (
    collect_d5_event_sources,
)
from app.services.payroll_d5_verified_review_snapshot import (
    _targets,
)
from app.services.payroll_d5_regulatory_review_bridge import (
    build_d5_regulatory_review_bridge,
    verify_d5_regulatory_review_bridge,
)
from app.services.payroll_d5_database_field_projection import (
    _canonical_digest,
)


class D5PeriodCoverageError(ValueError):
    pass


def _positive(value, name):
    if type(value) is not int or value <= 0:
        raise D5PeriodCoverageError("Invalid " + name)


def _target_event_id(target):
    kind = target["kind"]

    if kind in ("event", "category_transition"):
        return target["employment_event_id"]

    if kind == "correction_chain":
        return target["replacement_event_id"]

    raise D5PeriodCoverageError(
        "Unsupported D5 target kind"
    )


def _compare_coverage(sources, targets):
    """Require one review target per active HR event."""

    source_ids = [
        source.employment_event_id
        for source in sources
    ]

    if len(source_ids) != len(set(source_ids)):
        raise D5PeriodCoverageError(
            "Duplicate active PostgreSQL source"
        )

    target_ids = [
        _target_event_id(target)
        for target in targets
    ]

    if len(target_ids) != len(set(target_ids)):
        raise D5PeriodCoverageError(
            "Multiple review targets for one event"
        )

    missing = sorted(set(source_ids) - set(target_ids))
    extra = sorted(set(target_ids) - set(source_ids))

    if missing or extra:
        raise D5PeriodCoverageError(
            "Incomplete period coverage: "
            f"missing={len(missing)}, extra={len(extra)}"
        )

    return tuple(sorted(source_ids))


async def _load_period(db, company_id, period_id):
    period = await db.scalar(
        select(PayrollPeriod).where(
            PayrollPeriod.id == period_id,
            PayrollPeriod.company_id == company_id,
        ).execution_options(populate_existing=True)
    )

    if period is None:
        raise D5PeriodCoverageError(
            "Payroll period not found in company scope"
        )

    if period.start_date > period.end_date:
        raise D5PeriodCoverageError(
            "Invalid payroll period dates"
        )

    return period


async def build_d5_period_coverage_review(
    db,
    *,
    company_id,
    payroll_period_id,
    targets,
):
    """Compare every active period event with verified candidates."""

    _positive(company_id, "company_id")
    _positive(payroll_period_id, "payroll_period_id")

    try:
        normalized = [] if isinstance(targets, (list, tuple)) and not targets else _targets(targets)
    except Exception as exc:
        raise D5PeriodCoverageError(
            "Invalid D5 review targets"
        ) from exc

    period = await _load_period(
        db,
        company_id,
        payroll_period_id,
    )

    try:
        sources = await collect_d5_event_sources(
            db,
            company_id=company_id,
            date_from=period.start_date,
            date_to=period.end_date,
        )
    except Exception as exc:
        raise D5PeriodCoverageError(
            "Active PostgreSQL event inventory rejected"
        ) from exc

    active_ids = _compare_coverage(sources, normalized)

    if not active_ids:
        # Absence is independently re-read from the scoped database inventory.
        # It does not establish legal non-applicability or approve a D5 form.
        body = {
            "schema": "D5_EMPTY_PERIOD_COVERAGE_REVIEW_V1",
            "company_id": company_id,
            "payroll_period_id": payroll_period_id,
            "period_start": period.start_date.isoformat(),
            "period_end": period.end_date.isoformat(),
            "active_event_ids": [],
            "active_event_count": 0,
            "candidate_target_count": 0,
            "inventory_event_types": ["hire", "transfer", "termination"],
            "inventory_scope": "active_non_reversed_hr_events",
            "legal_non_applicability_confirmed": False,
            "bridge_sha256": None,
            "review_only": True,
            "normative_approval": False,
            "official_generation": "BLOCKED",
            "official_rows_generated": 0,
        }
        return {
            "body": body,
            "coverage_sha256": _canonical_digest(body),
            "bridge": None,
        }

    # The bridge independently verifies persisted professional
    # evidence, category transitions and correction chains.
    try:
        bridge = await build_d5_regulatory_review_bridge(
            db,
            company_id=company_id,
            payroll_period_id=payroll_period_id,
            targets=normalized,
        )

        verified = await verify_d5_regulatory_review_bridge(
            db, bridge
        )
    except Exception as exc:
        raise D5PeriodCoverageError(
            "D5/D6 source bridge rejected"
        ) from exc

    if verified is not True:
        raise D5PeriodCoverageError(
            "Regulatory bridge verification failed"
        )

    body = {
        "schema": "D5_PERIOD_COVERAGE_REVIEW_V1",
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "period_start": period.start_date.isoformat(),
        "period_end": period.end_date.isoformat(),
        "active_event_ids": list(active_ids),
        "active_event_count": len(active_ids),
        "candidate_target_count": len(normalized),
        "bridge_sha256": bridge["bridge_sha256"],
        "review_only": True,
        "normative_approval": False,
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }

    return {
        "body": body,
        "coverage_sha256": _canonical_digest(body),
        "bridge": bridge,
    }


async def verify_d5_period_coverage_review(db, review):
    """Recheck hash, exact target coverage and live sources."""

    if type(review) is not dict or set(review) != {
        "body", "coverage_sha256", "bridge"
    }:
        raise D5PeriodCoverageError(
            "Invalid coverage review envelope"
        )

    body = review["body"]

    if type(body) is not dict:
        raise D5PeriodCoverageError(
            "Invalid coverage review body"
        )

    if (
        not isinstance(review["coverage_sha256"], str)
        or _canonical_digest(body)
        != review["coverage_sha256"]
    ):
        raise D5PeriodCoverageError(
            "Coverage integrity mismatch"
        )

    if body.get("schema") == "D5_EMPTY_PERIOD_COVERAGE_REVIEW_V1":
        try:
            rebuilt = await build_d5_period_coverage_review(
                db,
                company_id=body["company_id"],
                payroll_period_id=body["payroll_period_id"],
                targets=[],
            )
        except Exception as exc:
            raise D5PeriodCoverageError(
                "Live empty period coverage verification failed"
            ) from exc
        if rebuilt != review or rebuilt["coverage_sha256"] != review["coverage_sha256"]:
            raise D5PeriodCoverageError(
                "Empty coverage no longer matches PostgreSQL"
            )
        return True

    if (
        body.get("schema") != "D5_PERIOD_COVERAGE_REVIEW_V1"
        or body.get("review_only") is not True
        or body.get("normative_approval") is not False
        or body.get("official_generation") != "BLOCKED"
        or type(body.get("official_rows_generated")) is not int
        or body["official_rows_generated"] != 0
    ):
        raise D5PeriodCoverageError(
            "Invalid review-only status"
        )

    bridge = review["bridge"]

    if (
        type(bridge) is not dict
        or type(bridge.get("body")) is not dict
    ):
        raise D5PeriodCoverageError(
            "Invalid bound regulatory bridge"
        )

    if bridge.get("bridge_sha256") != body.get(
        "bridge_sha256"
    ):
        raise D5PeriodCoverageError(
            "Regulatory bridge digest mismatch"
        )

    snapshot = bridge["body"].get("d5_source_snapshot")

    if (
        type(snapshot) is not dict
        or type(snapshot.get("body")) is not dict
        or type(snapshot["body"].get("entries")) is not list
    ):
        raise D5PeriodCoverageError(
            "Invalid D5 source entries"
        )

    try:
        rebuilt = await build_d5_period_coverage_review(
            db,
            company_id=body["company_id"],
            payroll_period_id=body["payroll_period_id"],
            targets=[
                entry["target"]
                for entry in snapshot["body"]["entries"]
            ],
        )
    except Exception as exc:
        raise D5PeriodCoverageError(
            "Live period coverage verification failed"
        ) from exc

    if rebuilt != review:
        raise D5PeriodCoverageError(
            "Coverage no longer matches PostgreSQL"
        )

    return True
