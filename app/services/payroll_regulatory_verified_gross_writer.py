"""Independent gross-source control for internal D1/4DF reviews.

This is NOT a full regulatory source verifier:
- tax components are not approved by this module;
- payment dates/remittances are not approved;
- D5/D6 professional and worktime evidence is not approved;
- official generation remains blocked.

The old lower-level snapshot writer is an internal primitive.
Use this entrypoint when independent payroll gross verification
is required.
"""

import json
from decimal import Decimal, InvalidOperation

from sqlalchemy import select, text

from app.services.payroll_regulatory_persisted_source_verifier import (
    PayrollCalculation,
    current_calculation_filter,
)

from app.services.payroll_regulatory_internal_snapshot_writer import (
    persist_internal_four_form_review,
)


class IndependentGrossSourceError(ValueError):
    pass


def _money(value):
    if (
        isinstance(value, bool)
        or isinstance(value, float)
        or value is None
    ):
        raise IndependentGrossSourceError(
            "Untrusted financial number"
        )

    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise IndependentGrossSourceError(
            "Invalid financial number"
        ) from None

    if not number.is_finite():
        raise IndependentGrossSourceError(
            "Nonfinite amount"
        )

    if number < 0:
        raise IndependentGrossSourceError(
            "Negative gross source"
        )

    if number != number.quantize(Decimal("0.01")):
        raise IndependentGrossSourceError(
            "Gross precision exceeds kopecks"
        )

    return number


async def derive_independent_gross_controls(
    db,
    *,
    company_id,
    payroll_period_id,
    payroll_period,
    report_references,
):
    """Requery current calculation gross and compare every D1/4DF row.

    Requires complete one-to-one source coverage by BOTH forms.
    Rejects ambiguous/missing calculations and unsupported row data.
    No totals are derived from the submitted reconciliation rows.
    """

    if (
        type(company_id) is not int
        or company_id <= 0
        or type(payroll_period_id) is not int
        or payroll_period_id <= 0
    ):
        raise IndependentGrossSourceError(
            "Invalid company/period"
        )

    if (
        not isinstance(report_references, dict)
        or set(report_references) != {
            "D1", "4DF", "D5", "D6"
        }
    ):
        raise IndependentGrossSourceError(
            "All four report references are mandatory"
        )

    if any(
        type(value) is not int or value <= 0
        for value in report_references.values()
    ):
        raise IndependentGrossSourceError(
            "Invalid report references"
        )

    # The existing writer uses this same lock. Holding it until
    # transaction completion serializes cooperating review writers.
    locked = (
        await db.execute(
            text("""
                SELECT id, year, month
                FROM payroll_periods
                WHERE company_id = :company_id
                  AND id = :period_id
                FOR UPDATE
            """),
            {
                "company_id": company_id,
                "period_id": payroll_period_id,
            },
        )
    ).mappings().one_or_none()

    if locked is None:
        raise IndependentGrossSourceError(
            "Payroll period does not exist"
        )

    actual_period = (
        f"{locked['year']:04d}-{locked['month']:02d}"
    )

    if actual_period != payroll_period:
        raise IndependentGrossSourceError(
            "Payroll month does not match persisted period"
        )

    result = await db.execute(
        select(
            PayrollCalculation.id,
            PayrollCalculation.gross_amount,
            PayrollCalculation.employment_contract_id,
        )
        .where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.payroll_period_id
                == payroll_period_id,
            current_calculation_filter(),
        )
        .order_by(PayrollCalculation.id)
        .with_for_update()
    )

    calculations = result.all()

    if not calculations:
        raise IndependentGrossSourceError(
            "No current persisted payroll calculations"
        )

    sources = {}
    contracts = set()
    total = Decimal("0.00")

    for calculation_id, gross, contract_id in calculations:
        if calculation_id in sources:
            raise IndependentGrossSourceError(
                "Duplicate current payroll calculation"
            )

        if contract_id in contracts:
            raise IndependentGrossSourceError(
                "Ambiguous current calculation per contract"
            )

        contracts.add(contract_id)

        checked_gross = _money(gross)
        sources[calculation_id] = (
            checked_gross,
            contract_id,
        )
        total += checked_gross

    totals = {}

    for form, kind in (("D1", "d1"), ("4DF", "4df")):
        report_id = report_references[form]

        header = (
            await db.execute(
                text("""
                    SELECT report_kind, payroll_period_id,
                           status
                    FROM payroll_regulatory_reports
                    WHERE company_id = :company_id
                      AND id = :report_id
                    FOR UPDATE
                """),
                {
                    "company_id": company_id,
                    "report_id": report_id,
                },
            )
        ).mappings().one_or_none()

        if (
            header is None
            or header["report_kind"] != kind
            or header["payroll_period_id"]
                != payroll_period_id
            or header["status"] != "draft"
        ):
            raise IndependentGrossSourceError(
                f"{form}: incorrect persisted report scope"
            )

        rows = (
            await db.execute(
                text("""
                    SELECT row_code, payload_json,
                           payroll_calculation_id,
                           employment_contract_id
                    FROM payroll_regulatory_report_rows
                    WHERE company_id = :company_id
                      AND payroll_regulatory_report_id = :report_id
                    ORDER BY row_no
                    FOR UPDATE
                """),
                {
                    "company_id": company_id,
                    "report_id": report_id,
                },
            )
        ).mappings().all()

        if len(rows) != len(sources):
            raise IndependentGrossSourceError(
                f"{form}: incomplete calculation source coverage"
            )

        seen = set()
        summed = Decimal("0.00")

        for row in rows:
            if row["row_code"] != (
                "ERP_INTERNAL_RECONCILIATION_V1"
            ):
                raise IndependentGrossSourceError(
                    f"{form}: unsupported internal row"
                )

            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, ValueError):
                raise IndependentGrossSourceError(
                    f"{form}: malformed source JSON"
                ) from None

            if not isinstance(payload, dict):
                raise IndependentGrossSourceError(
                    "Source payload must be an object"
                )

            if (
                payload.get("schema")
                != "erp.internal-regulatory-row.v1"
                or payload.get("form") != form
                or payload.get("company_id") != company_id
                or payload.get("payroll_period") != payroll_period
            ):
                raise IndependentGrossSourceError(
                    f"{form}: wrong source envelope"
                )

            source = payload.get("source")

            if (
                not isinstance(source, dict)
                or source.get("type")
                    != "payroll_calculation"
                or type(source.get("id")) is not int
            ):
                raise IndependentGrossSourceError(
                    f"{form}: source is not payroll calculation"
                )

            source_id = source["id"]

            if (
                source_id not in sources
                or source_id in seen
                or row["payroll_calculation_id"] != source_id
                or payload.get("payroll_calculation_id")
                    != source_id
            ):
                raise IndependentGrossSourceError(
                    f"{form}: missing, repeated or substituted source"
                )

            actual_gross, contract_id = sources[source_id]

            if row["employment_contract_id"] not in (
                None,
                contract_id,
            ):
                raise IndependentGrossSourceError(
                    f"{form}: contract mismatch"
                )

            if (
                payload.get("employment_contract_id")
                != row["employment_contract_id"]
            ):
                raise IndependentGrossSourceError(
                    f"{form}: JSON contract mismatch"
                )

            amounts = payload.get("amounts")

            # This phase deliberately verifies gross ONLY.
            # Tax amounts cannot be claimed as verified here.
            if (
                not isinstance(amounts, dict)
                or set(amounts) != {"gross"}
            ):
                raise IndependentGrossSourceError(
                    f"{form}: unsupported financial fields"
                )

            row_gross = _money(amounts["gross"])

            if row_gross != actual_gross:
                raise IndependentGrossSourceError(
                    f"{form}: gross differs from current payroll"
                )

            seen.add(source_id)
            summed += row_gross

        if seen != set(sources):
            raise IndependentGrossSourceError(
                f"{form}: incomplete source coverage"
            )

        if summed != total:
            raise IndependentGrossSourceError(
                f"{form}: independent gross mismatch"
            )

        totals[form] = {"gross": str(total)}

    # No invented financial totals for HR evidence.
    totals["D5"] = {}
    totals["D6"] = {}

    return totals


async def persist_gross_verified_internal_four_form_review(
    db,
    *,
    company_id,
    payroll_period_id,
    payroll_period,
    report_references,
    request_key,
):
    """Independent current-calculation gross gate + atomic writer."""

    independent_totals = await derive_independent_gross_controls(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        payroll_period=payroll_period,
        report_references=report_references,
    )

    result = await persist_internal_four_form_review(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        payroll_period=payroll_period,
        report_references=report_references,
        expected_source_totals=independent_totals,
        request_key=request_key,
    )

    return {
        **result,
        "independent_gross_verified": True,
        "tax_components_verified": False,
        "payment_evidence_verified": False,
        "hr_sources_verified": False,
        "official_generation": "BLOCKED",
    }
