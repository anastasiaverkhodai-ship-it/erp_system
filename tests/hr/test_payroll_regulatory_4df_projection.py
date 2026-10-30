import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from test_payroll_regulatory_dated_4df import case, build

from app.services.payroll_regulatory_4df_projection import (
    project_4df_financial_groups,
)
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)


def scenario():
    report, readiness, approvals, allocations = case()

    rows = [
        SimpleNamespace(
            company_id=report.company_id,
            row_no=i,
            row_code=item["row_code"],
            employee_id=item["employee_id"],
            payroll_calculation_id=item[
                "payroll_calculation_id"
            ],
            payload_json=item["payload_json"],
        )
        for i, item in enumerate(
            build(report, readiness, approvals, allocations), 1
        )
    ]

    return report, rows


def project(report, rows):
    return project_4df_financial_groups(
        company_id=report.company_id,
        payroll_period_id=report.payroll_period_id,
        rows=rows,
        expected_calculation_ids=[
            rows[0].payroll_calculation_id
        ],
    )


def test_two_partial_payments_become_one_review_group():
    report, rows = scenario()

    result = project(report, rows)

    assert len(result) == 1

    group = result[0]

    assert group["status"] == "REVIEW_ONLY_NOT_FOR_FILING"
    assert group["statutory_approved"] is False

    assert group["gross_accrued"] == "1000.00"
    assert group["pit_accrued"] == "180.00"
    assert group["levy_accrued"] == "50.00"
    assert group["paid_amount"] == "770.00"

    assert group["payment_dates"] == [
        "2026-10-20",
        "2026-10-25",
    ]

    assert len(group["financial_evidence"]) == 2
    assert len(group["source_fingerprint"]) == 64


def test_group_is_deterministic():
    report, rows = scenario()
    assert project(report, rows) == project(report, rows)


def test_cross_company_row_rejected():
    report, rows = scenario()
    rows[0].company_id += 1

    with pytest.raises(
        PayrollRegulatoryReportError
    ):
        project(report, rows)


def test_incomplete_payment_evidence_rejected():
    report, rows = scenario()

    with pytest.raises(
        PayrollRegulatoryReportError
    ):
        project(report, rows[:1])


def test_mismatched_income_category_rejected():
    report, rows = scenario()

    payload = json.loads(rows[1].payload_json)
    payload["category_code"] = "OTHER"

    rows[1].payload_json = json.dumps(payload)

    with pytest.raises(
        PayrollRegulatoryReportError,
        match="split across incompatible",
    ):
        project(report, rows)


def test_invalid_employee_rejected():
    report, rows = scenario()
    rows[0].employee_id = None

    with pytest.raises(
        PayrollRegulatoryReportError,
        match="Verified employee",
    ):
        project(report, rows)


def test_mutated_accrual_is_rejected():
    report, rows = scenario()

    payload = json.loads(rows[1].payload_json)
    payload["gross_accrued_amount"] = "1000.00"
    rows[1].payload_json = json.dumps(payload)

    with pytest.raises(
        PayrollRegulatoryReportError,
        match="do not reconcile",
    ):
        project(report, rows)
