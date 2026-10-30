"""Source-backed internal 4DF financial review export.

Not an official declaration, statutory mapping or filing format.
"""
import csv
import hashlib
import io
import json
from dataclasses import dataclass

from sqlalchemy import select

from app.models.payroll_regulatory_report import (
    PayrollRegulatoryReport,
    PayrollRegulatoryReportRow,
)
from app.services.payroll_regulatory_4df_form_contract import (
    validate_4df_financial_contract,
)
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)


EXPORT_COLUMNS = (
    "document_type",
    "company_id",
    "payroll_period_id",
    "report_revision",
    "payroll_calculation_id",
    "employee_id",
    "employment_contract_id",
    "source_sha256",
    "slice_number",
    "slice_count",
    "income_month",
    "payment_date",
    "category_code",
    "gross_accrued_amount",
    "pit_accrued_amount",
    "levy_accrued_amount",
    "paid_amount",
    "pit_remitted",
    "levy_remitted",
    "payment_evidence_reference",
    "tax_remittance_evidence_reference",
    "review_reference",
)


@dataclass(frozen=True)
class FourDFReviewExport:
    csv_text: str
    sha256: str
    row_count: int
    calculation_count: int


async def export_4df_financial_review(
    db,
    *,
    company_id: int,
    payroll_regulatory_report_id: int,
) -> FourDFReviewExport:
    report = await db.scalar(
        select(PayrollRegulatoryReport).where(
            PayrollRegulatoryReport.company_id == company_id,
            PayrollRegulatoryReport.id
                == payroll_regulatory_report_id,
        )
    )

    if report is None:
        raise PayrollRegulatoryReportError(
            "Regulatory report not found"
        )

    if report.report_kind != "4df" or report.status != "draft":
        raise PayrollRegulatoryReportError(
            "Only draft 4DF evidence can be exported for review"
        )

    rows = list((
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

    if any(
        row.row_code != "4DF_DATED_EVIDENCE_V1"
        for row in rows
    ):
        raise PayrollRegulatoryReportError(
            "Mixed or non-financial 4DF rows cannot be exported"
        )

    totals = validate_4df_financial_contract(
        company_id=company_id,
        payroll_period_id=report.payroll_period_id,
        rows=rows,
    )

    from app.services.payroll_regulatory_readiness_service import (
        payroll_regulatory_readiness,
    )

    readiness = await payroll_regulatory_readiness(
        db,
        company_id=company_id,
        payroll_period_id=report.payroll_period_id,
        report_kind="4df",
    )

    expected = {
        item["payroll_calculation_id"]
        for item in readiness["calculation_sources"]
    }

    if (
        not expected
        or len(expected) != len(readiness["calculation_sources"])
        or set(totals) != expected
    ):
        raise PayrollRegulatoryReportError(
            "Review export does not cover current payroll sources"
        )

    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=EXPORT_COLUMNS,
        extrasaction="ignore",
        lineterminator="\n",
    )
    writer.writeheader()

    for row in rows:
        payload = json.loads(row.payload_json)
        values = {
            "document_type": "INTERNAL_REVIEW_NOT_FOR_FILING",
            "company_id": company_id,
            "payroll_period_id": report.payroll_period_id,
            "report_revision": report.revision,
            "payroll_calculation_id": row.payroll_calculation_id,
            "employee_id": row.employee_id,
            "employment_contract_id": row.employment_contract_id,
            "source_sha256": payload["source_sha256"],
            "slice_number": payload["slice_number"],
            "slice_count": payload["slice_count"],
            "income_month": payload["income_month"],
            "payment_date": payload["payment_date"],
            "category_code": payload["category_code"],
            "gross_accrued_amount": payload["gross_accrued_amount"],
            "pit_accrued_amount": payload["pit_accrued_amount"],
            "levy_accrued_amount": payload["levy_accrued_amount"],
            "paid_amount": payload["paid_amount"],
            "pit_remitted": payload["pit_remitted"],
            "levy_remitted": payload["levy_remitted"],
            "payment_evidence_reference":
                payload["payment_evidence_reference"],
            "tax_remittance_evidence_reference":
                payload["tax_remittance_evidence_reference"],
            "review_reference": payload["review_reference"],
        }

        # Prevent spreadsheet formula injection in review exports.
        for key, value in values.items():
            if isinstance(value, str) and value.lstrip().startswith(
                ("=", "+", "-", "@")
            ):
                values[key] = "'" + value

        writer.writerow(values)

    csv_text = output.getvalue()
    digest = hashlib.sha256(
        csv_text.encode("utf-8")
    ).hexdigest()

    return FourDFReviewExport(
        csv_text=csv_text,
        sha256=digest,
        row_count=len(rows),
        calculation_count=len(totals),
    )
