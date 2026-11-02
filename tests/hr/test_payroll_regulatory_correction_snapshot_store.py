from copy import deepcopy

import pytest

from app.services.payroll_regulatory_correction_projection import (
    seal_projection,
)
from app.services.payroll_regulatory_correction_snapshot_store import (
    CorrectionSnapshotError,
    validate_snapshot_envelope,
)


def fixture():
    return {
        "schema": "payroll_internal_correction_projection_v1",
        "company_id": 1,
        "payroll_period_id": 2,
        "report_kind": "d1",
        "source_provenance": "PERSISTED_POSTGRESQL",
        "active_calculation_ids": [12],
        "excluded_historical_calculation_ids": [11],
        "components": [
            {
                "component": "personal_income_tax",
                "currency": "UAH",
                "original_base": "1000.00",
                "replacement_base": "1200.00",
                "base_delta": "200.00",
            }
        ],
        "official_form_fields_verified": False,
        "official_generation": "BLOCKED",
    }


def check(payload, report="d1"):
    return validate_snapshot_envelope(
        company_id=1,
        payroll_period_id=2,
        report_kind=report,
        sealed=seal_projection(payload),
    )


def test_positive_snapshot_scope():
    result = check(fixture())
    assert result["original_calculation_id"] == 11
    assert result["replacement_calculation_id"] == 12
    assert len(result["projection_sha256"]) == 64


def test_deterministic_digest():
    assert check(fixture()) == check(fixture())


def test_tampered_digest_rejected():
    sealed = seal_projection(fixture())
    sealed["payload"]["company_id"] = 999

    with pytest.raises(CorrectionSnapshotError):
        validate_snapshot_envelope(
            company_id=1,
            payroll_period_id=2,
            report_kind="d1",
            sealed=sealed,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "company",
        "period",
        "report",
        "schema",
        "provenance",
        "official",
        "fields",
        "history",
        "active",
        "same_revision",
        "multiple_replacements",
    ],
)
def test_reject_invalid_scope(mutation):
    payload = deepcopy(fixture())

    if mutation == "company":
        payload["company_id"] = 999
    elif mutation == "period":
        payload["payroll_period_id"] = 999
    elif mutation == "report":
        payload["report_kind"] = "4df"
    elif mutation == "schema":
        payload["schema"] = "unknown"
    elif mutation == "provenance":
        payload["source_provenance"] = "UNVERIFIED"
    elif mutation == "official":
        payload["official_generation"] = "READY"
    elif mutation == "fields":
        payload["official_form_fields_verified"] = True
    elif mutation == "history":
        payload["excluded_historical_calculation_ids"] = []
    elif mutation == "active":
        payload["active_calculation_ids"] = []
    elif mutation == "same_revision":
        payload["active_calculation_ids"] = [11]
    elif mutation == "multiple_replacements":
        payload["active_calculation_ids"] = [12, 13]

    with pytest.raises(CorrectionSnapshotError):
        check(payload)


def test_unsupported_report_rejected():
    with pytest.raises(CorrectionSnapshotError):
        check(fixture(), "d5")
