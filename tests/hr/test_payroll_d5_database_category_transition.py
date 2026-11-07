import pytest

from app.services.payroll_d5_database_category_transition import (
    D5CategoryTransitionError,
    _positive,
    _kind,
    _states,
)


class Contract:
    employment_kind = "internal_secondary"


class Event:
    before_state = {
        "employment_kind": "primary",
        "position_id": 1,
    }
    after_state = {
        "employment_kind": "internal_secondary",
        "position_id": 2,
    }


def test_valid_category_change():
    assert _states(Event(), Contract()) == (
        "primary", "internal_secondary"
    )


def test_no_category_change():
    event = Event()
    event.before_state = dict(event.after_state)
    with pytest.raises(
        D5CategoryTransitionError
    ):
        _states(event, Contract())


def test_same_report_category_not_transition():
    event = Event()
    event.before_state = {
        "employment_kind": "external_secondary",
        "position_id": 1,
    }
    with pytest.raises(
        D5CategoryTransitionError,
        match="No D5 category",
    ):
        _states(event, Contract())


def test_missing_historical_position():
    event = Event()
    event.before_state = {
        "employment_kind": "primary"
    }
    with pytest.raises(
        D5CategoryTransitionError,
        match="position",
    ):
        _states(event, Contract())


@pytest.mark.parametrize("value", [
    0, -1, True, None, "5",
])
def test_invalid_identifiers(value):
    with pytest.raises(D5CategoryTransitionError):
        _positive(value, "id")


def test_invalid_category():
    with pytest.raises(D5CategoryTransitionError):
        _kind("unknown")
