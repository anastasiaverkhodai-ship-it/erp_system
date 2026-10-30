from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.payroll_mutation_guard import serialized_payroll_mutation

from app.models.payroll import PayrollPeriod
from app.models.payroll_regulatory_report import (
    PayrollRegulatoryReport,
    PayrollRegulatoryReportKind,
    PayrollRegulatoryReportRow,
    PayrollRegulatoryReportStatus,
)


class PayrollRegulatoryReportError(ValueError):
    pass


class PayrollRegulatoryReportNotFoundError(
    PayrollRegulatoryReportError
):
    pass


def _kind_value(
    report_kind: PayrollRegulatoryReportKind | str,
) -> str:
    if isinstance(report_kind, PayrollRegulatoryReportKind):
        return report_kind.value

    value = str(report_kind).lower()

    valid = {
        item.value
        for item in PayrollRegulatoryReportKind
    }
    if value not in valid:
        raise PayrollRegulatoryReportError(
            f"Unsupported payroll regulatory report kind: {value}"
        )

    return value


async def _get_period(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_period_id: int,
) -> PayrollPeriod:
    period = await db.scalar(
        select(PayrollPeriod).where(
            PayrollPeriod.company_id == company_id,
            PayrollPeriod.id == payroll_period_id,
        )
    )

    if period is None:
        raise PayrollRegulatoryReportNotFoundError(
            "Payroll period not found"
        )

    return period


@serialized_payroll_mutation(PayrollRegulatoryReportError)
async def create_payroll_regulatory_report(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_period_id: int,
    report_kind: PayrollRegulatoryReportKind | str,
    created_by: int,
) -> PayrollRegulatoryReport:
    await _get_period(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
    )

    kind = _kind_value(report_kind)

    max_revision = await db.scalar(
        select(
            func.coalesce(
                func.max(PayrollRegulatoryReport.revision),
                0,
            )
        ).where(
            PayrollRegulatoryReport.company_id == company_id,
            PayrollRegulatoryReport.payroll_period_id
            == payroll_period_id,
            PayrollRegulatoryReport.report_kind == kind,
        )
    )

    report = PayrollRegulatoryReport(
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        report_kind=kind,
        revision=int(max_revision or 0) + 1,
        status=PayrollRegulatoryReportStatus.DRAFT.value,
        created_by=created_by,
    )

    db.add(report)
    await db.flush()
    await db.refresh(report)

    return report


@serialized_payroll_mutation(PayrollRegulatoryReportError)
async def generate_payroll_regulatory_report(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_regulatory_report_id: int,
    generated_by: int,
) -> PayrollRegulatoryReport:
    from datetime import datetime, timezone
    import hashlib
    import json

    from app.models.payroll import PayrollPeriodStatus

    from app.services.payroll_regulatory_readiness_service import (
        payroll_regulatory_readiness,
    )
    from app.services.payroll_regulatory_generated_integrity import (
        validate_mapped_report_rows,
        RegulatoryGeneratedIntegrityError,
    )
    from app.services.payroll_regulatory_mapping_registry import (
        approved_mappings_for,
        RegulatoryMappingError,
    )
    from app.services.payroll_regulatory_source_snapshot_service import (
        canonical_json,
        SOURCE_CODE,
    )
    from app.services.payroll_regulatory_source_reconciliation import (
        reconcile_regulatory_sources,
        RegulatorySourceReconciliationError,
    )

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
        raise PayrollRegulatoryReportNotFoundError(
            "Payroll regulatory report not found"
        )

    if report.status == PayrollRegulatoryReportStatus.GENERATED.value:
        if (
            report.generated_at is None
            or report.generated_by is None
        ):
            raise PayrollRegulatoryReportError(
                "Generated report metadata is corrupt"
            )
        return report

    if report.status != PayrollRegulatoryReportStatus.DRAFT.value:
        raise PayrollRegulatoryReportError(
            "Unsupported report state"
        )

    period = await _get_period(
        db,
        company_id=company_id,
        payroll_period_id=report.payroll_period_id,
    )

    if period.status != PayrollPeriodStatus.FINALIZED:
        raise PayrollRegulatoryReportError(
            "Regulatory report is incomplete: payroll period must be finalized"
        )

    readiness = await payroll_regulatory_readiness(
        db,
        company_id=company_id,
        payroll_period_id=report.payroll_period_id,
        report_kind=report.report_kind,
    )

    try:
        reconcile_regulatory_sources(readiness)
    except (
        RegulatorySourceReconciliationError,
        KeyError,
        TypeError,
    ) as exc:
        raise PayrollRegulatoryReportError(
            "Regulatory payroll source reconciliation failed"
        ) from exc

    if any(
        source.get("issues")
        for source in readiness["calculation_sources"]
    ):
        raise PayrollRegulatoryReportError(
            "Unresolved payroll source issues"
        )

    # Validate dated 4DF financial evidence before normative
    # mapping. This does not authorize statutory generation.
    if report.report_kind == "4df":
        from app.services.payroll_regulatory_4df_form_contract import (
            validate_4df_financial_contract,
        )

        dated_rows = list((
            await db.scalars(
                select(PayrollRegulatoryReportRow)
                .where(
                    PayrollRegulatoryReportRow.company_id
                        == company_id,
                    PayrollRegulatoryReportRow
                        .payroll_regulatory_report_id
                        == report.id,
                    PayrollRegulatoryReportRow.row_code
                        == "4DF_DATED_EVIDENCE_V1",
                )
                .order_by(PayrollRegulatoryReportRow.row_no)
            )
        ).all())

        if dated_rows:
            all_row_count = await db.scalar(
                select(func.count())
                .select_from(PayrollRegulatoryReportRow)
                .where(
                    PayrollRegulatoryReportRow.company_id
                        == company_id,
                    PayrollRegulatoryReportRow
                        .payroll_regulatory_report_id
                        == report.id,
                )
            )

            if all_row_count != len(dated_rows):
                raise PayrollRegulatoryReportError(
                    "Mixed dated 4DF evidence and other "
                    "report rows are not permitted"
                )

            validate_4df_financial_contract(
                company_id=company_id,
                payroll_period_id=report.payroll_period_id,
                rows=dated_rows,
                expected_calculation_ids=[
                    source["payroll_calculation_id"]
                    for source in readiness["calculation_sources"]
                ],
            )

            # The dated financial evidence has been checked,
            # but it is NOT an official statutory form.
            # Keep finalization explicitly blocked.
            raise PayrollRegulatoryReportError(
                "4DF financial evidence reconciled; "
                "independently approved statutory form "
                "mapping and conversion are still required"
            )

    try:
        approvals = approved_mappings_for(
            report.report_kind,
            period.start_date,
            period.end_date,
        )
    except RegulatoryMappingError as exc:
        raise PayrollRegulatoryReportError(str(exc)) from exc

    if not approvals:
        raise PayrollRegulatoryReportError(
            "Regulatory report is incomplete: "
            "no independently approved normative mappings"
        )

    if report.report_kind == "d5":
        from app.services.payroll_d5_trusted_classifier import (
            APPROVED_SOURCES,
        )
        if not APPROVED_SOURCES:
            raise PayrollRegulatoryReportError(
                "D5 trusted professional classifier not approved"
            )

    rows = list((
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

    try:
        validate_mapped_report_rows(
            report_kind=report.report_kind,
            company_id=company_id,
            period_id=report.payroll_period_id,
            rows=rows,
            approved_mapping_ids=frozenset(approvals),
        )
    except RegulatoryGeneratedIntegrityError as exc:
        raise PayrollRegulatoryReportError(
            "Regulatory report is incomplete: " + str(exc)
        ) from exc

    current_sources = {}

    for source in readiness["calculation_sources"]:
        calculation_id = source["payroll_calculation_id"]

        record = {
            "schema": SOURCE_CODE,
            "report_kind": report.report_kind,
            "company_id": company_id,
            "payroll_period_id": report.payroll_period_id,
            "source": source,
        }

        digest = hashlib.sha256(
            canonical_json(record).encode("utf-8")
        ).hexdigest()

        if calculation_id in current_sources:
            raise PayrollRegulatoryReportError(
                "Duplicate current payroll calculation"
            )

        current_sources[calculation_id] = (
            source,
            digest,
        )

    if not current_sources:
        raise PayrollRegulatoryReportError(
            "No verified payroll sources"
        )

    for row in rows:
        try:
            payload = json.loads(row.payload_json)
        except (ValueError, TypeError) as exc:
            raise PayrollRegulatoryReportError(
                "Invalid statutory row payload"
            ) from exc

        if not isinstance(payload, dict):
            raise PayrollRegulatoryReportError(
                "Invalid statutory row payload type"
            )

        source_id = payload["source_id"]
        mapping_id = payload["normative_mapping_id"]

        if row.payroll_calculation_id != source_id:
            raise PayrollRegulatoryReportError(
                "Statutory row calculation link mismatch"
            )

        linked = current_sources.get(source_id)

        if linked is None:
            raise PayrollRegulatoryReportError(
                "Statutory row references stale payroll source"
            )

        source, expected_digest = linked

        if payload["source_sha256"] != expected_digest:
            raise PayrollRegulatoryReportError(
                "Statutory row source fingerprint mismatch"
            )

        if (
            row.employee_id != source["employee_id"]
            or row.employment_contract_id
                != source["employment_contract_id"]
        ):
            raise PayrollRegulatoryReportError(
                "Statutory row employee or contract mismatch"
            )

        mapping = approvals[mapping_id]

        if (
            payload.get("normative_source_sha256")
            != mapping.source_sha256
        ):
            raise PayrollRegulatoryReportError(
                "Normative source digest mismatch"
            )

        if (
            payload.get("official_reference")
            != mapping.official_reference
        ):
            raise PayrollRegulatoryReportError(
                "Normative source reference mismatch"
            )

    # The registry and the existing integrity validator certify
    # approval/provenance. They do NOT by themselves certify the
    # statutory arithmetic or mandatory fields of D1/4DF/D5/D6.
    # Until the form-specific validator exists, fail closed.
    raise PayrollRegulatoryReportError(
        "Regulatory report is incomplete: "
        "form-specific statutory mapping and reconciliation "
        "must be implemented before finalization"
    )


async def list_payroll_regulatory_report_rows(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_regulatory_report_id: int,
) -> list[PayrollRegulatoryReportRow]:
    report = await db.scalar(
        select(PayrollRegulatoryReport.id).where(
            PayrollRegulatoryReport.company_id == company_id,
            PayrollRegulatoryReport.id
            == payroll_regulatory_report_id,
        )
    )

    if report is None:
        raise PayrollRegulatoryReportNotFoundError(
            "Payroll regulatory report not found"
        )

    return list(
        (
            await db.scalars(
                select(PayrollRegulatoryReportRow)
                .where(
                    PayrollRegulatoryReportRow.company_id
                    == company_id,
                    PayrollRegulatoryReportRow.payroll_regulatory_report_id
                    == payroll_regulatory_report_id,
                )
                .order_by(PayrollRegulatoryReportRow.row_no)
            )
        ).all()
    )
