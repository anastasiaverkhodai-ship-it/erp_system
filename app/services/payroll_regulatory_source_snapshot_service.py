"""Persist validated payroll evidence in draft regulatory reports.

SOURCE_EVIDENCE_V1 rows are internal evidence, never statutory form
rows. No declaration is generated or submitted by this service.
"""
import hashlib
import json
from decimal import Decimal

from sqlalchemy import select

from app.models.payroll_regulatory_report import (
    PayrollRegulatoryReport,
    PayrollRegulatoryReportRow,
)
from app.services.payroll_regulatory_readiness_service import (
    payroll_regulatory_readiness,
)
from app.services.payroll_regulatory_source_reconciliation import (
    reconcile_regulatory_sources,
    RegulatorySourceReconciliationError,
)
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)
from app.services.payroll_mutation_guard import (
    serialized_payroll_mutation,
)

SOURCE_CODE = "SOURCE_EVIDENCE_V1"


def canonical_json(value):
    def normalize(item):
        if isinstance(item, Decimal):
            if not item.is_finite():
                raise PayrollRegulatoryReportError(
                    "Non-finite monetary evidence"
                )
            return str(item)

        if isinstance(item, dict):
            if not all(isinstance(k, str) for k in item):
                raise PayrollRegulatoryReportError(
                    "Invalid source evidence key"
                )
            return {
                key: normalize(item[key])
                for key in sorted(item)
            }

        if isinstance(item, (tuple, list)):
            return [normalize(v) for v in item]

        if item is None or type(item) in (str, int, bool):
            return item

        raise PayrollRegulatoryReportError(
            "Unsupported evidence value"
        )

    return json.dumps(
        normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def prepare_source_snapshot(readiness, *, report):
    if (
        readiness.get("company_id") != report.company_id
        or readiness.get("payroll_period_id")
            != report.payroll_period_id
        or readiness.get("report_kind") != report.report_kind
    ):
        raise PayrollRegulatoryReportError(
            "Regulatory report identity mismatch"
        )

    if "PAYROLL_PERIOD_NOT_FINALIZED" in (
        readiness.get("blocking_issues") or []
    ):
        raise PayrollRegulatoryReportError(
            "Payroll period must be finalized"
        )

    try:
        totals = reconcile_regulatory_sources(readiness)
    except (
        RegulatorySourceReconciliationError,
        KeyError,
        TypeError,
    ) as exc:
        raise PayrollRegulatoryReportError(
            "Regulatory source reconciliation failed"
        ) from exc

    if totals.source_count == 0:
        raise PayrollRegulatoryReportError(
            "Cannot infer an empty statutory declaration"
        )

    sources = readiness["calculation_sources"]
    if len(sources) != totals.source_count:
        raise PayrollRegulatoryReportError(
            "Source count mismatch"
        )

    rows = []

    for source in sources:
        if source.get("issues"):
            raise PayrollRegulatoryReportError(
                "Unresolved source evidence"
            )

        record = {
            "schema": SOURCE_CODE,
            "report_kind": report.report_kind,
            "company_id": report.company_id,
            "payroll_period_id": report.payroll_period_id,
            "source": source,
        }

        payload = canonical_json(record)
        digest = hashlib.sha256(
            payload.encode("utf-8")
        ).hexdigest()

        rows.append({
            "employee_id": source["employee_id"],
            "employment_contract_id":
                source["employment_contract_id"],
            "payroll_calculation_id":
                source["payroll_calculation_id"],
            "row_code": SOURCE_CODE,
            "payload_json": canonical_json({
                "evidence_sha256": digest,
                "record": record,
            }),
        })

    return rows


@serialized_payroll_mutation(PayrollRegulatoryReportError)
async def persist_regulatory_source_snapshot(
    db,
    *,
    company_id: int,
    payroll_regulatory_report_id: int,
):
    report = await db.scalar(
        select(PayrollRegulatoryReport)
        .where(
            PayrollRegulatoryReport.company_id == company_id,
            PayrollRegulatoryReport.id
                == payroll_regulatory_report_id,
        )
        .with_for_update()
    )

    if report is None:
        raise PayrollRegulatoryReportError(
            "Regulatory report not found"
        )

    if report.status != "draft":
        raise PayrollRegulatoryReportError(
            "Generated reports cannot be rewritten"
        )

    readiness = await payroll_regulatory_readiness(
        db,
        company_id=company_id,
        payroll_period_id=report.payroll_period_id,
        report_kind=report.report_kind,
    )

    prepared = prepare_source_snapshot(
        readiness,
        report=report,
    )

    existing = (
        await db.scalars(
            select(PayrollRegulatoryReportRow)
            .where(
                PayrollRegulatoryReportRow.company_id
                    == company_id,
                PayrollRegulatoryReportRow.payroll_regulatory_report_id
                    == report.id,
            )
            .order_by(PayrollRegulatoryReportRow.row_no)
        )
    ).all()

    if existing:
        if len(existing) != len(prepared):
            raise PayrollRegulatoryReportError(
                "Existing snapshot differs from current sources"
            )

        for current, candidate in zip(existing, prepared):
            if (
                current.row_code != SOURCE_CODE
                or current.payload_json != candidate["payload_json"]
                or current.employee_id != candidate["employee_id"]
                or current.employment_contract_id
                    != candidate["employment_contract_id"]
                or current.payroll_calculation_id
                    != candidate["payroll_calculation_id"]
            ):
                raise PayrollRegulatoryReportError(
                    "Existing snapshot differs from current sources"
                )

        return existing

    for number, row in enumerate(prepared, start=1):
        db.add(PayrollRegulatoryReportRow(
            company_id=company_id,
            payroll_regulatory_report_id=report.id,
            row_no=number,
            **row,
        ))

    await db.flush()

    return list((
        await db.scalars(
            select(PayrollRegulatoryReportRow)
            .where(
                PayrollRegulatoryReportRow.company_id == company_id,
                PayrollRegulatoryReportRow.payroll_regulatory_report_id
                    == report.id,
            )
            .order_by(PayrollRegulatoryReportRow.row_no)
        )
    ).all())
