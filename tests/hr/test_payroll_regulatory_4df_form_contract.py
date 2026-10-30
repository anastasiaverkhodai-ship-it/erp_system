import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from test_payroll_regulatory_dated_4df import case, build

from app.services.payroll_regulatory_4df_form_contract import (
    validate_4df_financial_contract,
)
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)


def fixture():
    report, readiness, approvals, allocations = case()
    prepared = build(
        report, readiness, approvals, allocations
    )

    rows = [
        SimpleNamespace(
            company_id=report.company_id,
            row_no=i,
            row_code=item["row_code"],
            payroll_calculation_id=item[
                "payroll_calculation_id"
            ],
            payload_json=item["payload_json"],
        )
        for i, item in enumerate(prepared, 1)
    ]

    return report, rows


def validate(report, rows):
    return validate_4df_financial_contract(
        company_id=report.company_id,
        payroll_period_id=report.payroll_period_id,
        rows=rows,
    )


def mutate(rows, index, field, value):
    rows = deepcopy(rows)
    payload = json.loads(rows[index].payload_json)
    payload[field] = value
    rows[index].payload_json = json.dumps(payload)
    return rows


def test_real_dated_rows_reconcile():
    report, rows = fixture()
    result = validate(report, rows)
    assert len(result) == 1
    values = next(iter(result.values()))
    assert values["gross_accrued"] == "1000.00"
    assert values["paid"] == "770.00"
    assert values["unpaid_net"] == "0.00"
    assert values["payment_dates"] == [
        "2026-10-20",
        "2026-10-25",
    ]


@pytest.mark.parametrize(
    "index,field,value",
    [
        (1, "gross_accrued_amount", "1000.00"),
        (0, "pit_accrued_amount", "179.00"),
        (0, "paid_amount", "1000.00"),
        (1, "source_sha256", "b" * 64),
        (1, "slice_number", 1),
        (0, "currency_code", "EUR"),
        (0, "payment_evidence_reference", ""),
        (1, "accrual_attribution", "original"),
    ],
)
def test_invalid_financial_evidence_rejected(
    index, field, value
):
    report, rows = fixture()
    rows = mutate(rows, index, field, value)

    with pytest.raises(PayrollRegulatoryReportError):
        validate(report, rows)


def test_missing_slice_rejected():
    report, rows = fixture()

    with pytest.raises(
        PayrollRegulatoryReportError,
        match="Incomplete",
    ):
        validate(report, rows[:1])


def test_cross_company_rejected():
    report, rows = fixture()
    rows[0].company_id += 1

    with pytest.raises(
        PayrollRegulatoryReportError,
        match="cross-company",
    ):
        validate(report, rows)



def test_current_source_coverage_is_complete():
    report, rows = fixture()

    source_ids = {
        row.payroll_calculation_id
        for row in rows
    }

    result = validate_4df_financial_contract(
        company_id=report.company_id,
        payroll_period_id=report.payroll_period_id,
        rows=rows,
        expected_calculation_ids=source_ids,
    )

    assert set(result) == source_ids


def test_missing_current_payroll_source_rejected():
    report, rows = fixture()

    source_id = rows[0].payroll_calculation_id

    with pytest.raises(
        PayrollRegulatoryReportError,
        match="does not cover",
    ):
        validate_4df_financial_contract(
            company_id=report.company_id,
            payroll_period_id=report.payroll_period_id,
            rows=rows,
            expected_calculation_ids={
                source_id,
                source_id + 100000,
            },
        )


def test_duplicate_expected_payroll_sources_rejected():
    report, rows = fixture()
    source_id = rows[0].payroll_calculation_id

    with pytest.raises(
        PayrollRegulatoryReportError,
        match="Invalid expected",
    ):
        validate_4df_financial_contract(
            company_id=report.company_id,
            payroll_period_id=report.payroll_period_id,
            rows=rows,
            expected_calculation_ids=[
                source_id,
                source_id,
            ],
        )
