import pytest

from scripts.verify_payroll_snapshot_runtime_security import (
    sqlstate,
    validate_runtime_identity,
)


def good():
    return (
        "erp_db",
        "erp_user",
        "erp_user",
        "erp_snapshot_owner",
        (True, True, False, False, False),
        False,
        False,
    )


def test_correct_runtime_identity():
    validate_runtime_identity(*good())


@pytest.mark.parametrize("position,replacement", [
    (0, "postgres"),
    (1, "anastasia"),
    (2, "anastasia"),
    (3, "erp_user"),
    (4, (True, True, True, False, False)),
    (4, (True, False, False, False, False)),
    (4, (True, True, False, True, False)),
    (4, (True, True, False, False, True)),
    (5, True),
    (6, True),
])
def test_fail_closed_runtime_identity(position, replacement):
    values = list(good())
    values[position] = replacement

    with pytest.raises(AssertionError):
        validate_runtime_identity(*values)


def test_sqlstate_from_driver_error():
    class DriverError(Exception):
        sqlstate = "42501"

    class Wrapper(Exception):
        orig = DriverError()

    assert sqlstate(Wrapper()) == "42501"


def test_unknown_sqlstate_not_assumed_to_be_permission_denial():
    assert sqlstate(Exception("other SQL error")) is None
