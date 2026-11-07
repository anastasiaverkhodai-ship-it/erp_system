from types import SimpleNamespace

import pytest

from app.services.payroll_d5_period_coverage import (
    D5PeriodCoverageError,
    _compare_coverage,
    _target_event_id,
    _positive,
)


def source(event_id):
    return SimpleNamespace(
        employment_event_id=event_id
    )


def test_complete_coverage():
    targets = [
        {"kind": "event", "employment_event_id": 2},
        {"kind": "event", "employment_event_id": 1},
    ]
    assert _compare_coverage(
        [source(1), source(2)],
        targets,
    ) == (1, 2)


def test_missing_event_rejected():
    with pytest.raises(
        D5PeriodCoverageError,
        match="missing=1",
    ):
        _compare_coverage(
            [source(1), source(2)],
            [{"kind": "event", "employment_event_id": 1}],
        )


def test_extra_event_rejected():
    with pytest.raises(
        D5PeriodCoverageError,
        match="extra=1",
    ):
        _compare_coverage(
            [source(1)],
            [
                {"kind": "event", "employment_event_id": 1},
                {"kind": "event", "employment_event_id": 2},
            ],
        )


def test_duplicate_target_event_rejected():
    with pytest.raises(
        D5PeriodCoverageError,
        match="Multiple review targets",
    ):
        _compare_coverage(
            [source(1)],
            [
                {"kind": "event", "employment_event_id": 1},
                {
                    "kind": "category_transition",
                    "employment_event_id": 1,
                    "previous_evidence_id": 5,
                },
            ],
        )


def test_correction_target_uses_replacement_event():
    assert _target_event_id({
        "kind": "correction_chain",
        "original_event_id": 1,
        "reversal_event_id": 2,
        "replacement_event_id": 3,
    }) == 3


@pytest.mark.parametrize(
    "value", [0, -1, True, None, "5"]
)
def test_invalid_identifiers(value):
    with pytest.raises(D5PeriodCoverageError):
        _positive(value, "id")
