from copy import deepcopy
from datetime import date

import pytest

from app.services.payroll_regulatory_d5_d6_source_control import (
    D5D6SourceError,
    evidence_digest,
    prepare_source_evidence,
)


def case():
    return dict(
        table_name="hr_changes",
        company_id=7,
        period_start=date(2026, 10, 1),
        period_end=date(2026, 10, 31),
        records=[{
            "id": 11,
            "company_id": 7,
            "event_date": date(2026, 10, 15),
            "event_type": "test_event",
        }],
    )


def test_realistic_dated_source_inventory():
    result = prepare_source_evidence(**case())

    assert result["scanned_count"] == 1
    assert result["dated_in_period_count"] == 1
    assert result["dated_evidence"][0][
        "statutory_classification"
    ] == "NOT_VERIFIED"


def test_evidence_digest_is_deterministic():
    a = {"id": 1, "company_id": 7}
    b = {"company_id": 7, "id": 1}

    assert evidence_digest(a) == evidence_digest(b)

    b["company_id"] = 8
    assert evidence_digest(a) != evidence_digest(b)


def test_undated_source_is_not_classified():
    payload = case()
    del payload["records"][0]["event_date"]

    result = prepare_source_evidence(**payload)

    assert result["dated_in_period_count"] == 0
    assert result["undated_or_ambiguous_count"] == 1


def test_outside_period_not_misclassified():
    payload = case()
    payload["records"][0]["event_date"] = date(2026, 9, 15)

    result = prepare_source_evidence(**payload)

    assert result["dated_in_period_count"] == 0
    assert result["dated_outside_period_count"] == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "cross_company",
        "duplicate",
        "missing_id",
        "invalid_date",
        "invalid_period",
        "unknown_table",
    ],
)
def test_invalid_hr_sources_rejected(mutation):
    payload = deepcopy(case())

    if mutation == "cross_company":
        payload["records"][0]["company_id"] = 8
    elif mutation == "duplicate":
        payload["records"].append(
            deepcopy(payload["records"][0])
        )
    elif mutation == "missing_id":
        del payload["records"][0]["id"]
    elif mutation == "invalid_date":
        payload["records"][0]["event_date"] = "bad-date"
    elif mutation == "invalid_period":
        payload["period_start"] = date(2026, 11, 1)
    elif mutation == "unknown_table":
        payload["table_name"] = "unknown_events"

    with pytest.raises(D5D6SourceError):
        prepare_source_evidence(**payload)
