"""Create and persist D1/4DF draft rows from verified payroll sources.

Not an official declaration. The caller must supply independently
reviewed form-specific allocation evidence. No guessed statutory codes.
"""
import hashlib
import json
from datetime import date
from decimal import Decimal

from sqlalchemy import select

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
from app.services.payroll_regulatory_readiness_service import (
    payroll_regulatory_readiness,
)
from app.services.payroll_regulatory_source_reconciliation import (
    reconcile_regulatory_sources,
)
from app.services.payroll_regulatory_source_snapshot_service import (
    canonical_json,
)
from app.services.payroll_regulatory_mapping_registry import (
    approved_mappings_for,
)

ZERO = Decimal("0.00")
FORM_CODES = {"d1": "D1", "4df": "4DF"}


def money(value, field):
    try:
        if isinstance(value, bool):
            raise ValueError
        number = Decimal(str(value))
    except (ValueError, TypeError, ArithmeticError):
        raise PayrollRegulatoryReportError(
            f"Invalid {field}"
        )

    if (
        not number.is_finite()
        or number < ZERO
        or number.as_tuple().exponent < -2
    ):
        raise PayrollRegulatoryReportError(
            f"Invalid {field}"
        )

    return number


def required_date(value, field):
    if not isinstance(value, date) or (
        hasattr(value, "hour")
    ):
        raise PayrollRegulatoryReportError(
            f"Verified {field} required"
        )
    return value


def prepare_form_rows(
    *,
    report,
    readiness,
    evidence,
    approvals,
):
    """Build draft rows with verified one-to-one source allocation.

    Multiple income categories or payment dates per calculation must
    be represented separately by a future split-allocation engine;
    this writer rejects them rather than collapsing them.
    """
    kind = report.report_kind

    if kind not in FORM_CODES:
        raise PayrollRegulatoryReportError(
            "Only D1 and 4DF source-backed rows supported"
        )

    if not approvals:
        raise PayrollRegulatoryReportError(
            "Independent normative mapping approval required"
        )

    if not isinstance(evidence, (tuple, list)):
        raise PayrollRegulatoryReportError(
            "Reviewed form allocation evidence required"
        )

    if any(
        readiness.get(key) != getattr(report, key)
        for key in ("company_id", "payroll_period_id", "report_kind")
    ):
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
        row["payroll_calculation_id"]: row
        for row in readiness["calculation_sources"]
    }

    if (
        len(sources) != totals.source_count
        or not sources
    ):
        raise PayrollRegulatoryReportError(
            "Current payroll source set invalid"
        )

    if len(evidence) != len(sources):
        raise PayrollRegulatoryReportError(
            "One independently reviewed allocation per payroll "
            "calculation required"
        )

    result = []
    seen = set()

    for item in evidence:
        if not isinstance(item, dict):
            raise PayrollRegulatoryReportError(
                "Invalid reviewed allocation"
            )

        source_id = item.get("payroll_calculation_id")
        if type(source_id) is not int or source_id not in sources:
            raise PayrollRegulatoryReportError(
                "Unrecognized payroll source"
            )

        if source_id in seen:
            raise PayrollRegulatoryReportError(
                "Duplicate payroll allocation"
            )
        seen.add(source_id)

        source = sources[source_id]

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
                "Normative evidence does not match approval"
            )

        if not item.get("review_reference"):
            raise PayrollRegulatoryReportError(
                "Independent allocation review reference required"
            )

        category = item.get("category_code")
        if (
            not isinstance(category, str)
            or not category.strip()
        ):
            raise PayrollRegulatoryReportError(
                "Verified statutory category required"
            )

        accrual = money(
            item.get("accrued_amount"), "accrual"
        )

        if accrual != money(
            source["gross_amount"], "source gross"
        ):
            raise PayrollRegulatoryReportError(
                "Accrual does not match payroll source"
            )

        income_month = required_date(
            item.get("income_month"), "income month"
        )

        if income_month.day != 1:
            raise PayrollRegulatoryReportError(
                "Income month must be first calendar day"
            )

        source_record = {
            "schema": "SOURCE_EVIDENCE_V1",
            "company_id": report.company_id,
            "payroll_period_id": report.payroll_period_id,
            "report_kind": kind,
            "source": source,
        }

        digest = hashlib.sha256(
            canonical_json(source_record).encode("utf-8")
        ).hexdigest()

        fields = {
            "company_id": report.company_id,
            "payroll_period_id": report.payroll_period_id,
            "report_kind": kind,
            "source_id": source_id,
            "source_sha256": digest,
            "normative_mapping_id": mapping_id,
            "normative_source_sha256": mapping.source_sha256,
            "official_reference": mapping.official_reference,
            "review_reference": item["review_reference"],
            "category_code": category.strip(),
            "income_month": income_month.isoformat(),
            "accrued_amount": str(accrual),
            "currency_code": "UAH",
        }

        if kind == "d1":
            contribution = money(
                item.get("usc_amount"), "USC"
            )
            if contribution != money(
                source["employer_contribution_amount"],
                "source USC",
            ):
                raise PayrollRegulatoryReportError(
                    "USC does not match statutory result"
                )

            fields["usc_amount"] = str(contribution)

            insured_days = item.get("insured_days")
            if (
                type(insured_days) is not int
                or not 0 <= insured_days <= 31
            ):
                raise PayrollRegulatoryReportError(
                    "Verified insured days required"
                )

            fields["insured_days"] = insured_days

        else:
            pit = money(item.get("pit_accrued"), "PIT")
            levy = money(
                item.get("levy_accrued"), "military levy"
            )

            component_totals = {
                component: ZERO
                for component in (
                    "personal_income_tax",
                    "military_levy",
                )
            }

            for line in source["components"]:
                component = line["component"]
                if component in component_totals:
                    component_totals[component] += money(
                        line["amount"], component
                    )

            if (
                pit != component_totals["personal_income_tax"]
                or levy != component_totals["military_levy"]
            ):
                raise PayrollRegulatoryReportError(
                    "4DF taxes do not reconcile to statutory lines"
                )

            payment_date = required_date(
                item.get("payment_date"), "payment date"
            )

            paid = money(
                item.get("paid_amount"), "paid income"
            )
            pit_paid = money(
                item.get("pit_remitted"), "remitted PIT"
            )
            levy_paid = money(
                item.get("levy_remitted"), "remitted levy"
            )

            if (
                paid > accrual
                or pit_paid > pit
                or levy_paid > levy
            ):
                raise PayrollRegulatoryReportError(
                    "4DF payment exceeds verified accrual"
                )

            if not item.get("payment_evidence_reference"):
                raise PayrollRegulatoryReportError(
                    "Verified payment evidence required"
                )

            if not item.get("tax_remittance_evidence_reference"):
                raise PayrollRegulatoryReportError(
                    "Verified tax remittance evidence required"
                )

            fields.update({
                "pit_accrued": str(pit),
                "levy_accrued": str(levy),
                "paid_amount": str(paid),
                "payment_date": payment_date.isoformat(),
                "pit_remitted": str(pit_paid),
                "levy_remitted": str(levy_paid),
                "payment_evidence_reference":
                    item["payment_evidence_reference"],
                "tax_remittance_evidence_reference":
                    item["tax_remittance_evidence_reference"],
            })

        result.append({
            "company_id": report.company_id,
            "employee_id": source["employee_id"],
            "employment_contract_id":
                source["employment_contract_id"],
            "payroll_calculation_id": source_id,
            "row_code": FORM_CODES[kind] + "_REVIEWED_SOURCE_V1",
            "payload_json": canonical_json(fields),
        })

    if seen != set(sources):
        raise PayrollRegulatoryReportError(
            "Not every current payroll calculation is allocated"
        )

    return sorted(
        result,
        key=lambda row: (
            row["employee_id"],
            row["employment_contract_id"],
            row["payroll_calculation_id"],
        ),
    )


@serialized_payroll_mutation(PayrollRegulatoryReportError)
async def persist_reviewed_form_rows(
    db,
    *,
    company_id,
    payroll_regulatory_report_id,
    reviewed_allocations,
):
    report = await db.scalar(
        select(PayrollRegulatoryReport).where(
            PayrollRegulatoryReport.company_id == company_id,
            PayrollRegulatoryReport.id
                == payroll_regulatory_report_id,
        ).with_for_update()
    )

    if report is None:
        raise PayrollRegulatoryReportError(
            "Regulatory report not found"
        )

    if report.status != "draft":
        raise PayrollRegulatoryReportError(
            "Generated report is immutable"
        )

    from app.models.payroll import PayrollPeriod

    period = await db.scalar(
        select(PayrollPeriod).where(
            PayrollPeriod.company_id == company_id,
            PayrollPeriod.id == report.payroll_period_id,
        )
    )

    if period is None:
        raise PayrollRegulatoryReportError(
            "Payroll period not found"
        )

    approvals = approved_mappings_for(
        report.report_kind,
        period.start_date,
        period.end_date,
    )

    readiness = await payroll_regulatory_readiness(
        db,
        company_id=company_id,
        payroll_period_id=report.payroll_period_id,
        report_kind=report.report_kind,
    )

    if report.report_kind == "4df":
        from copy import deepcopy
        from app.services.payroll_regulatory_payment_evidence import (
            verify_4df_posted_payment_evidence,
        )

        reviewed_allocations = deepcopy(reviewed_allocations)

        verified_sources = {
            source["payroll_calculation_id"]: source
            for source in readiness["calculation_sources"]
        }

        if len(verified_sources) != len(
            readiness["calculation_sources"]
        ):
            raise PayrollRegulatoryReportError(
                "Duplicate payroll source in 4DF readiness"
            )

        for allocation in reviewed_allocations:
            if not isinstance(allocation, dict):
                raise PayrollRegulatoryReportError(
                    "Invalid 4DF allocation"
                )

            calculation_id = allocation.get(
                "payroll_calculation_id"
            )

            source = verified_sources.get(calculation_id)

            if source is None:
                raise PayrollRegulatoryReportError(
                    "4DF allocation has no current payroll source"
                )

            employee_id = source.get("employee_id")

            if type(employee_id) is not int or employee_id <= 0:
                raise PayrollRegulatoryReportError(
                    "Verified payroll employee required"
                )

            if (
                "employee_id" in allocation
                and allocation["employee_id"] != employee_id
            ):
                raise PayrollRegulatoryReportError(
                    "4DF allocation employee mismatch"
                )

            allocation["_verified_employee_id"] = employee_id

        await verify_4df_posted_payment_evidence(
            db,
            company_id=company_id,
            reviewed_allocations=reviewed_allocations,
        )

    prepared = prepare_form_rows(
        report=report,
        readiness=readiness,
        evidence=reviewed_allocations,
        approvals=approvals,
    )

    existing = list((
        await db.scalars(
            select(PayrollRegulatoryReportRow).where(
                PayrollRegulatoryReportRow.company_id
                    == company_id,
                PayrollRegulatoryReportRow
                    .payroll_regulatory_report_id == report.id,
            ).order_by(PayrollRegulatoryReportRow.row_no)
        )
    ).all())

    if existing:
        if len(existing) != len(prepared):
            raise PayrollRegulatoryReportError(
                "Existing report rows differ"
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
                    "Existing report rows differ"
                )

        return existing

    for index, candidate in enumerate(prepared, 1):
        db.add(PayrollRegulatoryReportRow(
            payroll_regulatory_report_id=report.id,
            row_no=index,
            **candidate,
        ))

    await db.flush()

    return list((
        await db.scalars(
            select(PayrollRegulatoryReportRow).where(
                PayrollRegulatoryReportRow.company_id
                    == company_id,
                PayrollRegulatoryReportRow
                    .payroll_regulatory_report_id == report.id,
            ).order_by(PayrollRegulatoryReportRow.row_no)
        )
    ).all())
