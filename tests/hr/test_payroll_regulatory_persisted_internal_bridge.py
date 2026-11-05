import json

import pytest

from app.services.payroll_regulatory_persisted_internal_bridge import (
    INTERNAL_ROW_CODE,
    INTERNAL_SCHEMA,
    PersistedRegulatoryBridgeError,
    assemble_persisted_internal_snapshot,
    row_from_persisted_record,
)


def record(form, row_id, source_amounts):
    payload = {
        "schema": INTERNAL_SCHEMA,
        "form": form,
        "company_id": 1,
        "payroll_period": "2026-10",
        "row_key": f"{form}-1",
        "employee_id": 10,
        "employment_contract_id": 20,
        "payroll_calculation_id": 30,
        "source": {
            "type": "payroll_calculation",
            "id": 30,
            "revision": 1,
        },
        "document_reference": "ERP-TEST-2026",
        "document_date": "2026-10-31",
        "amounts": source_amounts,
        "attributes": {
            "evidence_status": "internal_test_only"
        },
        "correction_of": None,
    }

    return {
        "id": row_id,
        "company_id": 1,
        "payroll_regulatory_report_id": row_id + 100,
        "row_no": 1,
        "row_code": INTERNAL_ROW_CODE,
        "employee_id": 10,
        "employment_contract_id": 20,
        "payroll_calculation_id": 30,
        "payload_json": json.dumps(payload),
    }


def scenario():
    rows = {
        "D1": [record("D1", 1, {"gross": "100.00"})],
        "4DF": [record("4DF", 2, {"gross": "100.00"})],
        "D5": [record("D5", 3, {})],
        "D6": [record("D6", 4, {})],
    }

    expected = {
        "D1": {"gross": "100.00"},
        "4DF": {"gross": "100.00"},
        "D5": {},
        "D6": {},
    }

    return rows, expected


def review(rows=None, expected=None):
    original_rows, original_expected = scenario()

    return assemble_persisted_internal_snapshot(
        company_id=1,
        payroll_period="2026-10",
        version=1,
        report_rows=(
            original_rows if rows is None else rows
        ),
        expected_source_totals=(
            original_expected
            if expected is None else expected
        ),
    )


def test_positive_four_form_reconciliation():
    snapshot = review()

    assert len(snapshot.sha256) == 64
    assert snapshot.official_generation_authorized is False
    assert snapshot.regulator_accepted is False
    assert snapshot.statutory_mapping_verified is False


def test_deterministic_replay():
    assert review().sha256 == review().sha256


def test_tampered_company_rejected():
    rows, _ = scenario()
    rows["D1"][0]["company_id"] = 2

    with pytest.raises(PersistedRegulatoryBridgeError):
        review(rows=rows)


def test_non_internal_report_row_rejected():
    rows, _ = scenario()
    rows["D1"][0]["row_code"] = "D1_UNKNOWN"

    with pytest.raises(PersistedRegulatoryBridgeError):
        review(rows=rows)


def test_relational_source_mismatch_rejected():
    rows, _ = scenario()
    rows["D1"][0]["payroll_calculation_id"] = 99

    with pytest.raises(PersistedRegulatoryBridgeError):
        review(rows=rows)


def test_cross_period_payload_rejected():
    rows, _ = scenario()
    payload = json.loads(rows["D1"][0]["payload_json"])
    payload["payroll_period"] = "2026-09"
    rows["D1"][0]["payload_json"] = json.dumps(payload)

    with pytest.raises(PersistedRegulatoryBridgeError):
        review(rows=rows)


def test_tampered_financial_source_total_rejected():
    rows, expected = scenario()
    expected["D1"]["gross"] = "101.00"

    with pytest.raises(PersistedRegulatoryBridgeError):
        review(rows=rows, expected=expected)


def test_missing_form_rejected():
    rows, _ = scenario()
    del rows["D6"]

    with pytest.raises(PersistedRegulatoryBridgeError):
        review(rows=rows)


def test_invalid_json_rejected():
    rows, _ = scenario()
    rows["4DF"][0]["payload_json"] = "{invalid"

    with pytest.raises(PersistedRegulatoryBridgeError):
        review(rows=rows)


def test_missing_employee_and_contract_rejected():
    rows, _ = scenario()
    rows["D6"][0]["employee_id"] = None
    rows["D6"][0]["employment_contract_id"] = None

    with pytest.raises(PersistedRegulatoryBridgeError):
        review(rows=rows)


def test_duplicate_persisted_row_number_rejected():
    rows, _ = scenario()
    additional = record("D1", 11, {"gross": "100.00"})
    additional["row_no"] = 1
    rows["D1"].append(additional)

    with pytest.raises(PersistedRegulatoryBridgeError):
        review(rows=rows)


def test_unknown_schema_cannot_be_promoted():
    rows, _ = scenario()
    payload = json.loads(rows["D1"][0]["payload_json"])
    payload["schema"] = "unknown"
    rows["D1"][0]["payload_json"] = json.dumps(payload)

    with pytest.raises(PersistedRegulatoryBridgeError):
        review(rows=rows)
