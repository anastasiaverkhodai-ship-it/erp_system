from datetime import date

import pytest

from app.services.payroll_d5_profession_history import (
    D5HistoryError,
    ProfessionEvent,
    build_candidate_history,
    candidate_profession_on_date,
    unresolved_date_conflicts,
)


DIGEST = "a" * 64


def event(
    operation,
    when,
    title="Професія",
    code="2419.2",
    candidates=None,
):
    if candidates is None:
        candidates = (when,)

    return ProfessionEvent(
        profession_code=code,
        operation=operation,
        effective_date=when,
        candidate_dates=candidates,
        title=None if operation == "cancel" else title,
        source_id="candidate_order",
        source_sha256=DIGEST,
        source_reference="Local evidence fixture",
    )


def test_addition_and_lookup():
    history = build_candidate_history([
        event("add", date(2010, 11, 1))
    ])

    assert history.event_count == 1
    assert history.normative_approved is False
    assert history.official_generation_authorized is False

    assert candidate_profession_on_date(
        history,
        code="2419.2",
        on_date=date(2011, 1, 1),
    ) is not None


def test_before_effective_date_missing():
    history = build_candidate_history([
        event("add", date(2010, 11, 1))
    ])

    assert candidate_profession_on_date(
        history,
        code="2419.2",
        on_date=date(2010, 10, 31),
    ) is None


def test_rename_closes_previous_period():
    history = build_candidate_history([
        event("add", date(2010, 11, 1), "Стара назва"),
        event("rename", date(2016, 3, 1), "Нова назва"),
    ])

    assert len(history.periods) == 2
    assert history.periods[0].valid_to == date(2016, 2, 29)
    assert history.periods[1].title == "Нова назва"


def test_cancellation_ends_profession():
    history = build_candidate_history([
        event("add", date(2010, 11, 1)),
        event("cancel", date(2016, 3, 1)),
    ])

    assert history.periods[0].valid_to == date(2016, 2, 29)

    assert candidate_profession_on_date(
        history,
        code="2419.2",
        on_date=date(2016, 3, 1),
    ) is None


def test_amendment_4_conflict_blocked():
    disputed = event(
        "add",
        date(2016, 3, 1),
        candidates=(
            date(2016, 3, 1),
            date(2016, 4, 1),
        ),
    )

    conflicts = unresolved_date_conflicts([disputed])

    assert len(conflicts) == 1
    assert conflicts[0]["resolution"] == "BLOCKED"

    with pytest.raises(
        D5HistoryError,
        match="Unresolved normative",
    ):
        build_candidate_history([disputed])


def test_missing_effective_date_blocked():
    disputed = ProfessionEvent(
        profession_code="2419.2",
        operation="add",
        effective_date=None,
        candidate_dates=(date(2016, 3, 1),),
        title="Професія",
        source_id="candidate",
        source_sha256=DIGEST,
        source_reference="Evidence fixture",
    )

    with pytest.raises(D5HistoryError, match="Unresolved"):
        build_candidate_history([disputed])


def test_duplicate_effective_date_rejected():
    with pytest.raises(D5HistoryError, match="one effective date"):
        build_candidate_history([
            event("add", date(2016, 3, 1)),
            event("rename", date(2016, 3, 1)),
        ])


def test_rename_before_addition_rejected():
    with pytest.raises(D5HistoryError, match="begin"):
        build_candidate_history([
            event("rename", date(2016, 3, 1))
        ])


def test_double_addition_rejected():
    with pytest.raises(D5HistoryError, match="already active"):
        build_candidate_history([
            event("add", date(2010, 11, 1)),
            event("add", date(2016, 3, 1)),
        ])


def test_cancel_then_rename_rejected():
    with pytest.raises(D5HistoryError, match="inactive"):
        build_candidate_history([
            event("add", date(2010, 11, 1)),
            event("cancel", date(2016, 3, 1)),
            event("rename", date(2017, 1, 1)),
        ])


def test_invalid_digest_rejected():
    invalid = ProfessionEvent(
        profession_code="2419.2",
        operation="add",
        effective_date=date(2016, 3, 1),
        candidate_dates=(date(2016, 3, 1),),
        title="Професія",
        source_id="source",
        source_sha256="invalid",
        source_reference="source",
    )

    with pytest.raises(D5HistoryError, match="SHA256"):
        build_candidate_history([invalid])


def test_different_codes_are_independent():
    history = build_candidate_history([
        event("add", date(2010, 11, 1), code="2419.2"),
        event("add", date(2011, 1, 1), code="1234"),
    ])

    assert len(history.periods) == 2
    assert history.official_generation_authorized is False
