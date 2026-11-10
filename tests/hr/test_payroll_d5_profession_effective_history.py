from datetime import date
from dataclasses import replace

import pytest

from app.services.payroll_d5_profession_effective_history import (
    D5ProfessionHistoryChange,
    D5ProfessionHistoryError,
    review_d5_profession_history,
)


def event(kind, day, code="2419.2"):
    return D5ProfessionHistoryChange(
        profession_code=code,
        change_kind=kind,
        effective_date=day,
        source_reference="SYNTHETIC-TEST-ONLY",
        source_sha256="a" * 64,
    )


def review(records, day):
    return review_d5_profession_history(
        profession_code="2419.2",
        reporting_date=day,
        changes=records,
    )


def history():
    return [
        event("introduced", date(2012, 9, 1)),
        event("removed", date(2017, 11, 1)),
        event("restored", date(2017, 11, 30)),
    ]


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2012, 8, 31), "NOT_VERIFIED"),
        (date(2012, 9, 1), "CANDIDATE_ACTIVE"),
        (date(2017, 10, 31), "CANDIDATE_ACTIVE"),
        (date(2017, 11, 1), "CANDIDATE_INACTIVE"),
        (date(2017, 11, 29), "CANDIDATE_INACTIVE"),
        (date(2017, 11, 30), "CANDIDATE_ACTIVE"),
    ],
)
def test_effective_dates(day, expected):
    result = review(history(), day)
    assert result["candidate_state"] == expected
    assert result["normative_approval"] == "NOT_APPROVED"
    assert result["official_generation"] == "BLOCKED"


def test_unknown_history():
    result = review([], date(2026, 11, 10))
    assert result["candidate_state"] == "NOT_VERIFIED"


def test_reproducible_digest():
    first = review(history(), date(2017, 12, 1))
    second = review(
        list(reversed(history())),
        date(2017, 12, 1),
    )
    assert first["history_sha256"] == second["history_sha256"]


def test_conflicting_same_date():
    with pytest.raises(D5ProfessionHistoryError):
        review([
            event("introduced", date(2017, 1, 1)),
            event("removed", date(2017, 1, 1)),
        ], date(2017, 1, 1))


def test_repeated_activation():
    with pytest.raises(D5ProfessionHistoryError):
        review([
            event("introduced", date(2012, 9, 1)),
            event("restored", date(2017, 11, 30)),
        ], date(2017, 11, 30))


def test_repeated_removal():
    with pytest.raises(D5ProfessionHistoryError):
        review([
            event("removed", date(2017, 11, 1)),
            event("removed", date(2017, 11, 30)),
        ], date(2017, 11, 30))


def test_cross_profession():
    with pytest.raises(D5ProfessionHistoryError):
        review([
            event("introduced", date(2012, 9, 1)),
            event("removed", date(2017, 11, 1), "9999.9"),
        ], date(2017, 11, 30))


def test_invalid_sha():
    with pytest.raises(D5ProfessionHistoryError):
        review([
            replace(
                event("introduced", date(2012, 9, 1)),
                source_sha256="invalid",
            )
        ], date(2012, 9, 1))


@pytest.mark.parametrize("bad", [None, True, "2017-11-30", 20171130])
def test_invalid_date(bad):
    with pytest.raises(D5ProfessionHistoryError):
        review(history(), bad)
