from decimal import Decimal
from types import SimpleNamespace
import json
import pytest

from app.services.payroll_regulatory_source_snapshot_service import (
    canonical_json,
    prepare_source_snapshot,
    SOURCE_CODE,
)
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)
from test_payroll_regulatory_source_reconciliation import example


def report():
    return SimpleNamespace(
        company_id=1,
        payroll_period_id=10,
        report_kind="d1",
    )


def valid():
    data = example()
    data["blocking_issues"] = [
        "DATED_STATUTORY_FORM_MAPPING_MISSING"
    ]
    return data


def test_real_source_evidence_snapshot():
    rows = prepare_source_snapshot(valid(), report=report())
    assert len(rows) == 1
    assert rows[0]["row_code"] == SOURCE_CODE
    payload = json.loads(rows[0]["payload_json"])
    assert len(payload["evidence_sha256"]) == 64
    assert payload["record"]["source"]["gross_amount"] == "1000.00"


def test_snapshot_is_deterministic():
    first = prepare_source_snapshot(valid(), report=report())
    second = prepare_source_snapshot(valid(), report=report())
    assert first == second


def test_other_company_rejected():
    data = valid()
    data["company_id"] = 2
    with pytest.raises(
        PayrollRegulatoryReportError,
        match="identity mismatch",
    ):
        prepare_source_snapshot(data, report=report())


def test_unfinalized_period_rejected():
    data = valid()
    data["blocking_issues"].append(
        "PAYROLL_PERIOD_NOT_FINALIZED"
    )
    with pytest.raises(
        PayrollRegulatoryReportError,
        match="finalized",
    ):
        prepare_source_snapshot(data, report=report())


def test_inconsistent_financial_source_rejected():
    data = valid()
    data["calculation_sources"][0]["net_amount"] = (
        Decimal("999.00")
    )
    with pytest.raises(
        PayrollRegulatoryReportError,
        match="reconciliation failed",
    ):
        prepare_source_snapshot(data, report=report())


def test_unsupported_json_value_rejected():
    with pytest.raises(
        PayrollRegulatoryReportError,
        match="Unsupported evidence value",
    ):
        canonical_json({"invalid": object()})
