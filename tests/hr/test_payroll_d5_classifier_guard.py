
"""Classifier guard tests using synthetic, not officially verified entries."""
from dataclasses import replace
from datetime import date

import pytest

from app.services.payroll_d5_professional_history import (
    D5ProfessionalHistoryRecord,
)
from app.services.payroll_d5_classifier_guard import (
    D5ClassifierEntry,
    D5ClassifierError,
    verify_d5_classifier,
)


def sample():
    history = D5ProfessionalHistoryRecord(
        company_id=1,
        employee_id=2,
        employment_contract_id=3,
        position_id=4,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        documented_on=date(2026, 1, 1),
        evidence_reference="synthetic-document",
        professional_name="Synthetic Profession",
        profession_code="1234",
        position_name="Synthetic Position",
        employment_category="primary",
    )
    entry = D5ClassifierEntry(
        profession_code="1234",
        professional_name="Synthetic Profession",
        effective_from=date(2026, 1, 1),
        effective_to=None,
        source_reference="synthetic-reviewed-source",
        source_version="synthetic-v1",
        reviewed=True,
    )
    return history, entry


def test_explicit_reviewed_registry_match():
    history, entry = sample()
    result = verify_d5_classifier(
        history=history,
        event_date=date(2026, 8, 1),
        entries=(entry,),
    )
    assert result.profession_code == "1234"
    assert result.source_version == "synthetic-v1"


@pytest.mark.parametrize(
    "change",
    [
        {"profession_code": "9999"},
        {"professional_name": "Incorrect name"},
        {"reviewed": False},
        {"effective_from": date(2026, 9, 1)},
        {"effective_to": date(2026, 7, 31)},
        {"source_reference": ""},
        {"source_version": ""},
    ],
)
def test_invalid_registry_match_rejected(change):
    history, entry = sample()
    with pytest.raises(D5ClassifierError):
        verify_d5_classifier(
            history=history,
            event_date=date(2026, 8, 1),
            entries=(replace(entry, **change),),
        )


def test_missing_registry_rejected():
    history, entry = sample()
    with pytest.raises(D5ClassifierError):
        verify_d5_classifier(
            history=history,
            event_date=date(2026, 8, 1),
            entries=(),
        )


def test_ambiguous_duplicate_registry_rejected():
    history, entry = sample()
    with pytest.raises(D5ClassifierError):
        verify_d5_classifier(
            history=history,
            event_date=date(2026, 8, 1),
            entries=(entry, entry),
        )


def test_synthetic_code_rejected():
    history, entry = sample()
    with pytest.raises(D5ClassifierError):
        verify_d5_classifier(
            history=replace(history, profession_code="TEST-ONLY"),
            event_date=date(2026, 8, 1),
            entries=(entry,),
        )


def test_dotted_profession_code_is_supported():
    history, entry = sample()
    history = replace(history, profession_code="2419.2")
    entry = replace(entry, profession_code="2419.2")
    result = verify_d5_classifier(
        history=history,
        event_date=date(2026, 8, 1),
        entries=(entry,),
    )
    assert result.profession_code == "2419.2"


@pytest.mark.parametrize(
    "bad_code",
    ["", "2419.", "2419.22", "24.19", "TEST", "２４１９"],
)
def test_invalid_profession_code_shapes_rejected(bad_code):
    history, entry = sample()
    with pytest.raises(D5ClassifierError):
        verify_d5_classifier(
            history=replace(history, profession_code=bad_code),
            event_date=date(2026, 8, 1),
            entries=(entry,),
        )
