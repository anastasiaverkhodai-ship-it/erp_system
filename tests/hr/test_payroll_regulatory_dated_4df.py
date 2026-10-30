from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace as NS

import pytest

from test_payroll_regulatory_source_reconciliation import example
from app.services.payroll_regulatory_mapping_registry import (
    ApprovedRegulatoryMapping,
)
from app.services.payroll_regulatory_dated_4df_service import (
    build_dated_4df_rows,
)
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)


def case():
    report = NS(
        company_id=1,
        payroll_period_id=10,
        report_kind="4df",
    )
    readiness = example()
    readiness["report_kind"] = "4df"
    readiness["blocking_issues"] = []

    approval = ApprovedRegulatoryMapping(
        mapping_id=17,
        report_kind="4df",
        effective_from=date(2026, 1, 1),
        effective_to=None,
        official_reference="TEST-ONLY",
        source_sha256="a" * 64,
        approved=True,
    )

    base = {
        "payroll_calculation_id": 4,
        "normative_mapping_id": 17,
        "normative_source_sha256": "a" * 64,
        "official_reference": "TEST-ONLY",
        "review_reference": "TEST-REVIEW",
        "category_code": "TEST-CATEGORY",
        "income_month": date(2026, 10, 1),
        "payment_evidence_reference": "TEST-POSTED-PAYMENT",
        "tax_remittance_evidence_reference":
            "TEST-POSTED-REMITTANCE",
    }

    first = {
        **base,
        "payment_date": date(2026, 10, 20),
        "paid_amount": D("300.00"),
        "pit_remitted": D("80.00"),
        "levy_remitted": D("20.00"),
        "payroll_disbursement_ids": [11],
        "tax_remittance_line_ids": [21, 22],
    }
    second = {
        **base,
        "payment_date": date(2026, 10, 25),
        "paid_amount": D("470.00"),
        "pit_remitted": D("100.00"),
        "levy_remitted": D("30.00"),
        "payroll_disbursement_ids": [12],
        "tax_remittance_line_ids": [23, 24],
    }

    return report, readiness, {17: approval}, [first, second]


def build(report, readiness, approvals, allocations):
    return build_dated_4df_rows(
        report=report,
        readiness=readiness,
        approvals=approvals,
        allocations=allocations,
    )


def test_two_dates_create_two_distinct_draft_rows():
    rows = build(*case())
    assert len(rows) == 2
    assert rows[0]["row_code"] == "4DF_DATED_EVIDENCE_V1"
    assert rows[0]["payload_json"] != rows[1]["payload_json"]


def test_dated_rows_are_deterministic():
    report, readiness, approvals, allocations = case()
    assert build(report, readiness, approvals, allocations) == (
        build(report, readiness, approvals, allocations[::-1])
    )


def test_overpayment_rejected():
    report, readiness, approvals, allocations = case()
    allocations[1]["paid_amount"] = D("500.00")
    with pytest.raises(
        PayrollRegulatoryReportError,
        match="exceed",
    ):
        build(report, readiness, approvals, allocations)


def test_duplicate_payment_ids_rejected():
    report, readiness, approvals, allocations = case()
    allocations[1]["payroll_disbursement_ids"] = [11]
    with pytest.raises(
        PayrollRegulatoryReportError,
        match="Duplicated",
    ):
        build(report, readiness, approvals, allocations)


def test_duplicate_tax_line_ids_rejected():
    report, readiness, approvals, allocations = case()
    allocations[1]["tax_remittance_line_ids"] = [21]
    with pytest.raises(
        PayrollRegulatoryReportError,
        match="Duplicated",
    ):
        build(report, readiness, approvals, allocations)


def test_unapproved_mapping_rejected():
    report, readiness, _, allocations = case()
    with pytest.raises(
        PayrollRegulatoryReportError,
        match="Independent normative",
    ):
        build(report, readiness, {}, allocations)


def test_mixed_income_categories_rejected():
    report, readiness, approvals, allocations = case()
    allocations[1]["category_code"] = "OTHER-CATEGORY"
    with pytest.raises(
        PayrollRegulatoryReportError,
        match="Mixed income",
    ):
        build(report, readiness, approvals, allocations)



def test_multiple_dates_do_not_duplicate_accrued_income():
    import json

    rows = build(*case())
    payloads = [
        json.loads(row["payload_json"])
        for row in rows
    ]

    assert len(payloads) == 2

    assert [
        row["accrual_attribution"]
        for row in payloads
    ] == ["original", "already_counted"]

    assert [
        row["gross_accrued_amount"]
        for row in payloads
    ] == ["1000.00", "0.00"]

    assert [
        row["pit_accrued_amount"]
        for row in payloads
    ] == ["180.00", "0.00"]

    assert [
        row["levy_accrued_amount"]
        for row in payloads
    ] == ["50.00", "0.00"]

    assert sum(
        D(row["gross_accrued_amount"])
        for row in payloads
    ) == D("1000.00")

    assert sum(
        D(row["pit_accrued_amount"])
        for row in payloads
    ) == D("180.00")

    assert sum(
        D(row["levy_accrued_amount"])
        for row in payloads
    ) == D("50.00")

    assert sum(
        D(row["paid_amount"])
        for row in payloads
    ) == D("770.00")


def test_accrual_attribution_independent_of_input_order():
    import json

    report, readiness, approvals, allocations = case()

    forward = build(
        report, readiness, approvals, allocations
    )
    backward = build(
        report, readiness, approvals, allocations[::-1]
    )

    assert forward == backward

    payloads = [
        json.loads(row["payload_json"])
        for row in forward
    ]

    assert payloads[0]["payment_date"] == "2026-10-20"
    assert payloads[0]["gross_accrued_amount"] == "1000.00"
    assert payloads[1]["payment_date"] == "2026-10-25"
    assert payloads[1]["gross_accrued_amount"] == "0.00"
