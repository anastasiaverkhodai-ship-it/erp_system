"""Dated, source-backed 4DF draft allocation.

This is a financial evidence schedule, NOT a generated statutory
declaration. It rejects unapproved mappings and incomplete sources.
"""
import hashlib
from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models.payroll import PayrollPeriod, PayrollPeriodStatus
from app.models.payroll_regulatory_report import (
    PayrollRegulatoryReport,
    PayrollRegulatoryReportRow,
)
from app.services.payroll_mutation_guard import (
    serialized_payroll_mutation,
)
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)
from app.services.payroll_regulatory_form_row_writer import (
    money,
    required_date,
)
from app.services.payroll_regulatory_source_reconciliation import (
    reconcile_regulatory_sources,
)
from app.services.payroll_regulatory_source_snapshot_service import (
    canonical_json,
)
from app.services.payroll_regulatory_readiness_service import (
    payroll_regulatory_readiness,
)
from app.services.payroll_regulatory_mapping_registry import (
    approved_mappings_for,
)
from app.services.payroll_regulatory_payment_evidence import (
    verify_4df_posted_payment_evidence,
)

ZERO = Decimal("0.00")
CODE = "4DF_DATED_EVIDENCE_V1"


def build_dated_4df_rows(
    *,
    report,
    readiness,
    allocations,
    approvals,
):
    if report.report_kind != "4df":
        raise PayrollRegulatoryReportError(
            "Dated allocation requires 4DF report"
        )

    if not approvals:
        raise PayrollRegulatoryReportError(
            "Independent normative mapping approval required"
        )

    for key in (
        "company_id",
        "payroll_period_id",
        "report_kind",
    ):
        if readiness.get(key) != getattr(report, key):
            raise PayrollRegulatoryReportError(
                "Report identity mismatch"
            )

    if "PAYROLL_PERIOD_NOT_FINALIZED" in (
        readiness.get("blocking_issues") or ()
    ):
        raise PayrollRegulatoryReportError(
            "Payroll period must be finalized"
        )

    totals = reconcile_regulatory_sources(readiness)
    sources = {
        item["payroll_calculation_id"]: item
        for item in readiness["calculation_sources"]
    }

    if len(sources) != totals.source_count or not sources:
        raise PayrollRegulatoryReportError(
            "Invalid current payroll source set"
        )

    if not isinstance(allocations, (tuple, list)):
        raise PayrollRegulatoryReportError(
            "Reviewed dated allocations required"
        )

    groups = defaultdict(list)
    payment_ids = set()
    tax_line_ids = set()

    for item in allocations:
        if not isinstance(item, dict):
            raise PayrollRegulatoryReportError(
                "Invalid dated allocation"
            )

        source_id = item.get("payroll_calculation_id")

        if type(source_id) is not int or source_id not in sources:
            raise PayrollRegulatoryReportError(
                "Unknown payroll calculation"
            )

        payment_date = required_date(
            item.get("payment_date"), "payment date"
        )
        income_month = required_date(
            item.get("income_month"), "income month"
        )

        if income_month.day != 1:
            raise PayrollRegulatoryReportError(
                "Income month must start on day one"
            )

        mapping_id = item.get("normative_mapping_id")
        if type(mapping_id) is not int or mapping_id not in approvals:
            raise PayrollRegulatoryReportError(
                "Unapproved normative mapping"
            )

        mapping = approvals[mapping_id]
        if (
            item.get("official_reference")
                != mapping.official_reference
            or item.get("normative_source_sha256")
                != mapping.source_sha256
        ):
            raise PayrollRegulatoryReportError(
                "Normative source evidence mismatch"
            )

        if not isinstance(item.get("review_reference"), str) or (
            not item["review_reference"].strip()
        ):
            raise PayrollRegulatoryReportError(
                "Independent review reference required"
            )

        category = item.get("category_code")
        if not isinstance(category, str) or not category.strip():
            raise PayrollRegulatoryReportError(
                "Verified income category required"
            )

        for field, used in (
            ("payroll_disbursement_ids", payment_ids),
            ("tax_remittance_line_ids", tax_line_ids),
        ):
            identifiers = item.get(field)
            if (
                not isinstance(identifiers, list)
                or not identifiers
                or any(
                    type(value) is not int or value <= 0
                    for value in identifiers
                )
                or len(set(identifiers)) != len(identifiers)
            ):
                raise PayrollRegulatoryReportError(
                    f"Invalid {field}"
                )

            if used.intersection(identifiers):
                raise PayrollRegulatoryReportError(
                    f"Duplicated {field} across dated allocations"
                )
            used.update(identifiers)

        for field in (
            "paid_amount",
            "pit_remitted",
            "levy_remitted",
        ):
            money(item.get(field), field)

        groups[source_id].append(item)

    if set(groups) != set(sources):
        raise PayrollRegulatoryReportError(
            "Every current payroll calculation must be reviewed"
        )

    result = []

    for source_id in sorted(groups):
        source = sources[source_id]
        slices = sorted(
            groups[source_id],
            key=lambda row: (
                row["payment_date"],
                min(row["payroll_disbursement_ids"]),
            ),
        )

        gross = money(source["gross_amount"], "gross")
        source_net = money(source["net_amount"], "net")

        component_totals = {
            "personal_income_tax": ZERO,
            "military_levy": ZERO,
        }

        for component in source["components"]:
            name = component["component"]
            if name in component_totals:
                component_totals[name] += money(
                    component["amount"], "statutory tax"
                )

        base_category = (
            slices[0]["category_code"],
            slices[0]["income_month"],
            slices[0]["normative_mapping_id"],
        )

        paid_total = ZERO
        pit_total = ZERO
        levy_total = ZERO

        for slice_number, item in enumerate(slices, start=1):
            category = (
                item["category_code"],
                item["income_month"],
                item["normative_mapping_id"],
            )

            if category != base_category:
                raise PayrollRegulatoryReportError(
                    "Mixed income categories or months require "
                    "separately verified income allocations"
                )

            paid_total += money(
                item["paid_amount"], "paid amount"
            )
            pit_total += money(
                item["pit_remitted"], "PIT remittance"
            )
            levy_total += money(
                item["levy_remitted"], "levy remittance"
            )

        if (
            paid_total > source_net
            or pit_total > component_totals["personal_income_tax"]
            or levy_total > component_totals["military_levy"]
        ):
            raise PayrollRegulatoryReportError(
                "Dated payments or tax remittances exceed "
                "verified payroll amounts"
            )

        record = {
            "schema": "SOURCE_EVIDENCE_V1",
            "company_id": report.company_id,
            "payroll_period_id": report.payroll_period_id,
            "report_kind": "4df",
            "source": source,
        }
        source_hash = hashlib.sha256(
            canonical_json(record).encode("utf-8")
        ).hexdigest()

        for slice_number, item in enumerate(slices, start=1):
            mapping = approvals[item["normative_mapping_id"]]

            # Financial allocation, not a statutory form line.
            # Income and accrued taxes are counted exactly once.
            # Payment and remittance amounts remain dated.
            first_slice = slice_number == 1

            accrued_gross = gross if first_slice else ZERO
            accrued_pit = (
                component_totals["personal_income_tax"]
                if first_slice else ZERO
            )
            accrued_levy = (
                component_totals["military_levy"]
                if first_slice else ZERO
            )

            payload = {
                "schema": CODE,
                "company_id": report.company_id,
                "payroll_period_id": report.payroll_period_id,
                "report_kind": "4df",
                "source_id": source_id,
                "source_sha256": source_hash,
                "normative_mapping_id": mapping.mapping_id,
                "normative_source_sha256": mapping.source_sha256,
                "official_reference": mapping.official_reference,
                "review_reference": item["review_reference"],
                "category_code": item["category_code"].strip(),
                "income_month": item["income_month"].isoformat(),
                "payment_date": item["payment_date"].isoformat(),
                "slice_number": slice_number,
                "slice_count": len(slices),
                "accrual_attribution": (
                    "original" if first_slice else "already_counted"
                ),
                "gross_accrued_amount": str(accrued_gross),
                "pit_accrued_amount": str(accrued_pit),
                "levy_accrued_amount": str(accrued_levy),
                "paid_amount": str(
                    money(item["paid_amount"], "paid")
                ),
                "pit_remitted": str(
                    money(item["pit_remitted"], "PIT")
                ),
                "levy_remitted": str(
                    money(item["levy_remitted"], "levy")
                ),
                "payment_evidence_reference":
                    item["payment_evidence_reference"],
                "tax_remittance_evidence_reference":
                    item["tax_remittance_evidence_reference"],
                "gross_source_total": str(gross),
                "net_source_total": str(source_net),
                "pit_source_total": str(
                    component_totals["personal_income_tax"]
                ),
                "levy_source_total": str(
                    component_totals["military_levy"]
                ),
                "currency_code": "UAH",
            }

            result.append({
                "company_id": report.company_id,
                "employee_id": source["employee_id"],
                "employment_contract_id":
                    source["employment_contract_id"],
                "payroll_calculation_id": source_id,
                "row_code": CODE,
                "payload_json": canonical_json(payload),
            })

    return result


@serialized_payroll_mutation(PayrollRegulatoryReportError)
async def persist_dated_4df_rows(
    db,
    *,
    company_id: int,
    payroll_regulatory_report_id: int,
    reviewed_allocations: list[dict],
):
    from copy import deepcopy

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

    if report.status != "draft" or report.report_kind != "4df":
        raise PayrollRegulatoryReportError(
            "Only draft 4DF reports can receive dated rows"
        )

    period = await db.scalar(
        select(PayrollPeriod).where(
            PayrollPeriod.company_id == company_id,
            PayrollPeriod.id == report.payroll_period_id,
        )
    )

    if period is None or period.status != PayrollPeriodStatus.FINALIZED:
        raise PayrollRegulatoryReportError(
            "Payroll period must be finalized"
        )

    readiness = await payroll_regulatory_readiness(
        db,
        company_id=company_id,
        payroll_period_id=report.payroll_period_id,
        report_kind="4df",
    )

    approvals = approved_mappings_for(
        "4df",
        period.start_date,
        period.end_date,
    )

    allocations = deepcopy(reviewed_allocations)
    sources = {
        item["payroll_calculation_id"]: item
        for item in readiness["calculation_sources"]
    }

    for item in allocations:
        if not isinstance(item, dict):
            raise PayrollRegulatoryReportError(
                "Invalid dated allocation"
            )

        source = sources.get(item.get("payroll_calculation_id"))
        if source is None:
            raise PayrollRegulatoryReportError(
                "Unrecognized payroll calculation"
            )

        if (
            "employee_id" in item
            and item["employee_id"] != source["employee_id"]
        ):
            raise PayrollRegulatoryReportError(
                "Employee identity mismatch"
            )

        item["_verified_employee_id"] = source["employee_id"]

    await verify_4df_posted_payment_evidence(
        db,
        company_id=company_id,
        reviewed_allocations=allocations,
    )

    prepared = build_dated_4df_rows(
        report=report,
        readiness=readiness,
        allocations=allocations,
        approvals=approvals,
    )

    existing = list((
        await db.scalars(
            select(PayrollRegulatoryReportRow)
            .where(
                PayrollRegulatoryReportRow.company_id == company_id,
                PayrollRegulatoryReportRow
                    .payroll_regulatory_report_id == report.id,
            )
            .order_by(PayrollRegulatoryReportRow.row_no)
        )
    ).all())

    if existing:
        if len(existing) != len(prepared):
            raise PayrollRegulatoryReportError(
                "Dated 4DF snapshot differs"
            )

        for stored, candidate in zip(existing, prepared):
            if any(
                getattr(stored, field) != candidate[field]
                for field in (
                    "company_id",
                    "employee_id",
                    "employment_contract_id",
                    "payroll_calculation_id",
                    "row_code",
                    "payload_json",
                )
            ):
                raise PayrollRegulatoryReportError(
                    "Dated 4DF snapshot differs"
                )

        return existing

    for number, candidate in enumerate(prepared, 1):
        db.add(PayrollRegulatoryReportRow(
            payroll_regulatory_report_id=report.id,
            row_no=number,
            **candidate,
        ))

    await db.flush()

    return list((
        await db.scalars(
            select(PayrollRegulatoryReportRow)
            .where(
                PayrollRegulatoryReportRow.company_id == company_id,
                PayrollRegulatoryReportRow
                    .payroll_regulatory_report_id == report.id,
            )
            .order_by(PayrollRegulatoryReportRow.row_no)
        )
    ).all())
