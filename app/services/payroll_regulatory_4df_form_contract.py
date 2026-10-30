"""Financial reconciliation contract for dated 4DF evidence.

The output is not a statutory declaration. Approval of official
form fields, income codes, applicability and reporting periods
is a separate mandatory step.
"""
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal

from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)
from app.services.payroll_regulatory_form_row_writer import money


REQUIRED = {
    "company_id",
    "payroll_period_id",
    "report_kind",
    "source_id",
    "source_sha256",
    "slice_number",
    "slice_count",
    "income_month",
    "payment_date",
    "gross_accrued_amount",
    "pit_accrued_amount",
    "levy_accrued_amount",
    "paid_amount",
    "pit_remitted",
    "levy_remitted",
    "gross_source_total",
    "net_source_total",
    "pit_source_total",
    "levy_source_total",
    "payment_evidence_reference",
    "tax_remittance_evidence_reference",
    "currency_code",
}


def validate_4df_financial_contract(
    *,
    company_id: int,
    payroll_period_id: int,
    rows,
    expected_calculation_ids=None,
):
    """Validate a complete multi-date financial evidence schedule.

    Does not infer statutory codes, calculate legal eligibility,
    or authorize regulatory report finalization.
    """
    if not isinstance(rows, (list, tuple)) or not rows:
        raise PayrollRegulatoryReportError(
            "4DF financial rows required"
        )

    groups = defaultdict(list)
    seen = set()
    previous = 0

    for row in rows:
        if row.company_id != company_id:
            raise PayrollRegulatoryReportError(
                "4DF cross-company evidence"
            )

        if row.row_no != previous + 1:
            raise PayrollRegulatoryReportError(
                "4DF non-contiguous row numbering"
            )
        previous = row.row_no

        if row.row_code != "4DF_DATED_EVIDENCE_V1":
            raise PayrollRegulatoryReportError(
                "Expected dated 4DF financial evidence"
            )

        try:
            payload = json.loads(row.payload_json)
        except (TypeError, ValueError) as exc:
            raise PayrollRegulatoryReportError(
                "Invalid 4DF evidence JSON"
            ) from exc

        if not isinstance(payload, dict):
            raise PayrollRegulatoryReportError(
                "Invalid 4DF evidence object"
            )

        missing = REQUIRED - payload.keys()
        if missing:
            raise PayrollRegulatoryReportError(
                "Missing 4DF financial evidence: "
                + ", ".join(sorted(missing))
            )

        if (
            payload["company_id"] != company_id
            or payload["payroll_period_id"] != payroll_period_id
            or payload["report_kind"] != "4df"
            or payload["currency_code"] != "UAH"
            or payload["source_id"] != row.payroll_calculation_id
        ):
            raise PayrollRegulatoryReportError(
                "4DF source identity mismatch"
            )

        source_id = payload["source_id"]
        number = payload["slice_number"]
        count = payload["slice_count"]

        if (
            type(source_id) is not int
            or source_id <= 0
            or type(number) is not int
            or type(count) is not int
            or count <= 0
            or not 1 <= number <= count
        ):
            raise PayrollRegulatoryReportError(
                "Invalid 4DF dated slice"
            )

        key = (source_id, number)

        if key in seen:
            raise PayrollRegulatoryReportError(
                "Duplicate 4DF dated slice"
            )
        seen.add(key)

        digest = payload["source_sha256"]

        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise PayrollRegulatoryReportError(
                "Invalid 4DF source fingerprint"
            )

        for field in (
            "income_month",
            "payment_date",
        ):
            try:
                parsed = date.fromisoformat(payload[field])
            except (TypeError, ValueError) as exc:
                raise PayrollRegulatoryReportError(
                    f"Invalid {field}"
                ) from exc

            if field == "income_month" and parsed.day != 1:
                raise PayrollRegulatoryReportError(
                    "Income month must begin on day one"
                )

        for field in (
            "payment_evidence_reference",
            "tax_remittance_evidence_reference",
        ):
            value = payload[field]
            if not isinstance(value, str) or not value.strip():
                raise PayrollRegulatoryReportError(
                    f"Missing {field}"
                )

        groups[source_id].append(payload)

    result = {}

    for source_id, items in sorted(groups.items()):
        items.sort(key=lambda item: item["slice_number"])

        count = items[0]["slice_count"]

        if len(items) != count or [
            item["slice_number"] for item in items
        ] != list(range(1, count + 1)):
            raise PayrollRegulatoryReportError(
                "Incomplete 4DF dated slices"
            )

        immutable = (
            "source_sha256",
            "income_month",
            "gross_source_total",
            "net_source_total",
            "pit_source_total",
            "levy_source_total",
        )

        for item in items[1:]:
            if any(
                item[field] != items[0][field]
                for field in immutable
            ):
                raise PayrollRegulatoryReportError(
                    "Inconsistent 4DF calculation source"
                )

        source_gross = money(
            items[0]["gross_source_total"], "source gross"
        )
        source_net = money(
            items[0]["net_source_total"], "source net"
        )
        source_pit = money(
            items[0]["pit_source_total"], "source PIT"
        )
        source_levy = money(
            items[0]["levy_source_total"], "source levy"
        )

        totals = {
            name: sum(
                (
                    money(item[field], field)
                    for item in items
                ),
                Decimal("0.00"),
            )
            for name, field in {
                "gross": "gross_accrued_amount",
                "pit": "pit_accrued_amount",
                "levy": "levy_accrued_amount",
                "paid": "paid_amount",
                "pit_remitted": "pit_remitted",
                "levy_remitted": "levy_remitted",
            }.items()
        }

        if (
            totals["gross"] != source_gross
            or totals["pit"] != source_pit
            or totals["levy"] != source_levy
        ):
            raise PayrollRegulatoryReportError(
                "4DF accrued amounts do not reconcile"
            )

        if (
            totals["paid"] > source_net
            or totals["pit_remitted"] > source_pit
            or totals["levy_remitted"] > source_levy
        ):
            raise PayrollRegulatoryReportError(
                "4DF payments exceed verified source totals"
            )

        if (
            items[0].get("accrual_attribution") != "original"
            or any(
                item.get("accrual_attribution")
                    != "already_counted"
                for item in items[1:]
            )
        ):
            raise PayrollRegulatoryReportError(
                "Invalid 4DF accrual attribution"
            )

        result[source_id] = {
            "gross_accrued": str(totals["gross"]),
            "pit_accrued": str(totals["pit"]),
            "levy_accrued": str(totals["levy"]),
            "paid": str(totals["paid"]),
            "pit_remitted": str(totals["pit_remitted"]),
            "levy_remitted": str(totals["levy_remitted"]),
            "unpaid_net": str(
                source_net - totals["paid"]
            ),
            "payment_dates": [
                item["payment_date"]
                for item in items
            ],
        }

    if expected_calculation_ids is not None:
        expected = list(expected_calculation_ids)

        if (
            not expected
            or any(
                type(value) is not int or value <= 0
                for value in expected
            )
            or len(set(expected)) != len(expected)
        ):
            raise PayrollRegulatoryReportError(
                "Invalid expected 4DF source set"
            )

        if set(result) != set(expected):
            raise PayrollRegulatoryReportError(
                "Dated 4DF evidence does not cover "
                "all current payroll calculations"
            )

    return result
