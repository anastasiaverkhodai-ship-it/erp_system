"""Source-bound D5 payroll readiness.

Combines live payroll review with candidate document evidence.
Never authorizes statutory filing, changes payroll, or writes rows.
"""

from datetime import date

from app.services.payroll_regulatory_readiness_service import (
    payroll_regulatory_readiness,
)

from app.services.payroll_d5_normative_acceptance import (
    review_d5_annex_normative_bundle,
)

from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)


async def payroll_d5_source_bound_readiness(
    db,
    *,
    company_id,
    payroll_period_id,
    source_root,
    manifest_paths,
    candidates,
    reporting_date,
    source_documents=None,
):
    """Return a fail-closed internal D5 payroll/source review."""

    if type(reporting_date) is not date:
        raise PayrollRegulatoryReportError(
            "Exact D5 reporting date required"
        )

    bundle = review_d5_annex_normative_bundle(
        source_root=source_root,
        manifest_paths=manifest_paths,
        candidates=candidates,
        reporting_date=reporting_date,
        source_documents=source_documents,
    )

    if (
        type(bundle) is not dict
        or bundle.get("official_generation") != "BLOCKED"
        or bundle.get("official_rows_generated") != 0
        or bundle.get("normative_mapping") != "NOT_APPROVED"
        or bundle.get("independent_authenticity") != "NOT_VERIFIED"
        or type(bundle.get("blockers")) not in (tuple, list)
    ):
        raise PayrollRegulatoryReportError(
            "D5 candidate evidence must remain non-authorizing"
        )

    if not all(
        type(issue) is str and bool(issue)
        for issue in bundle["blockers"]
    ):
        raise PayrollRegulatoryReportError(
            "Invalid D5 evidence blockers"
        )

    readiness = await payroll_regulatory_readiness(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        report_kind="d5",
    )

    if (
        type(readiness) is not dict
        or readiness.get("report_kind") != "d5"
        or readiness.get("company_id") != company_id
        or readiness.get("payroll_period_id") != payroll_period_id
        or readiness.get("ready") is not False
    ):
        raise PayrollRegulatoryReportError(
            "Unexpected D5 payroll readiness identity"
        )

    previous_issues = readiness.get("blocking_issues")

    if (
        type(previous_issues) not in (tuple, list)
        or not all(
            type(issue) is str and bool(issue)
            for issue in previous_issues
        )
    ):
        raise PayrollRegulatoryReportError(
            "Invalid payroll readiness blockers"
        )

    issues = list(dict.fromkeys(
        [
            *previous_issues,
            *bundle["blockers"],
            "D5_REPORTING_FORM_VERSION_NOT_APPROVED",
            "D5_OFFICIAL_EVENT_FIELD_MAPPING_NOT_APPROVED",
        ]
    ))

    return {
        **readiness,
        "ready": False,
        "blocking_issues": issues,
        "d5_source_bound_review": bundle,
        "d5_reporting_date": reporting_date.isoformat(),
        "d5_reporting_date_basis":
            "CALLER_SUPPLIED_NOT_NORMATIVELY_VERIFIED",
        "d5_normative_mapping": "NOT_APPROVED",
        "d5_independent_authenticity": "NOT_VERIFIED",
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
        "notice": (
            "Д5: внутрішня перевірка кадрових джерел, "
            "додатків та кандидатів нормативних документів. "
            "Не є офіційним звітом."
        ),
    }
