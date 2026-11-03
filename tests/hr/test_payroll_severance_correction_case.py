from datetime import date

import pytest

from app.services.payroll_severance_correction_case_service import (
    SeveranceCorrectionCaseError,
    validate_case_request,
)


def valid():
    return dict(
        company_id=1,
        payroll_calculation_id=2,
        as_of=date(2026, 9, 30),
        evidence_date=date(2026, 9, 20),
        evidence_reference="CORRECTION-DOCUMENT-2026-001",
        request_key="severance-correction-001",
        actor_id=3,
    )


def test_documented_request():
    assert validate_case_request(**valid())[
        "payroll_calculation_id"
    ] == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("company_id", 0),
        ("company_id", True),
        ("payroll_calculation_id", -1),
        ("actor_id", 0),
        ("as_of", "2026-09-30"),
        ("evidence_date", "2026-09-20"),
        ("evidence_reference", "short"),
        ("request_key", "x"),
    ],
)
def test_bad_requests_fail_closed(field, value):
    payload = valid()
    payload[field] = value

    with pytest.raises(SeveranceCorrectionCaseError):
        validate_case_request(**payload)


def test_future_evidence_rejected():
    payload = valid()
    payload["evidence_date"] = date(2026, 10, 1)

    with pytest.raises(
        SeveranceCorrectionCaseError,
        match="after assessment",
    ):
        validate_case_request(**payload)
