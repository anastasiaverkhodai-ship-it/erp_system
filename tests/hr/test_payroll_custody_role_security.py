from copy import deepcopy

import pytest

from app.services.payroll_custody_role_security import (
    TABLES,
    CustodyRoleSecurityError,
    evaluate_custody_role,
)


def good_rows():
    return [
        {
            "table_name": name,
            "runtime_role": "erp_user",
            "owner_role": "erp_snapshot_owner",
            "is_superuser": False,
            "bypass_rls": False,
            "is_owner": False,
            "inherits_owner": False,
            "can_assume_owner": False,
            "can_select": True,
            "can_insert": True,
            "can_update": False,
            "can_delete": False,
            "can_truncate": False,
            "immutable_trigger_enabled": True,
        }
        for name in TABLES
    ]


def test_three_tables_pass():
    result = evaluate_custody_role(good_rows())
    assert result["table_count"] == 3
    assert (
        result["security_status"]
        == "RESTRICTED_CUSTODY_ROLE_VERIFIED"
    )
    assert result["official_generation"] == "BLOCKED"


@pytest.mark.parametrize(
    "field,value",
    [
        ("is_superuser", True),
        ("bypass_rls", True),
        ("is_owner", True),
        ("inherits_owner", True),
        ("can_assume_owner", True),
        ("can_update", True),
        ("can_delete", True),
        ("can_truncate", True),
        ("can_insert", False),
        ("immutable_trigger_enabled", False),
    ],
)
def test_unsafe_privilege_rejected(field, value):
    rows = good_rows()
    rows[0][field] = value
    assert evaluate_custody_role(
        rows
    )["security_status"] == "BLOCKED"


def test_missing_table_rejected():
    assert evaluate_custody_role(
        good_rows()[:2]
    )["security_status"] == "BLOCKED"


def test_duplicate_table_rejected():
    rows = good_rows()
    rows.append(deepcopy(rows[0]))

    with pytest.raises(CustodyRoleSecurityError):
        evaluate_custody_role(rows)


def test_missing_evidence_rejected():
    with pytest.raises(CustodyRoleSecurityError):
        evaluate_custody_role(None)


def test_database_strings_compared_by_value():
    rows = good_rows()
    rows[0]["runtime_role"] = "".join(
        ["erp", "_user"]
    )
    rows[0]["owner_role"] = "".join(
        ["erp", "_snapshot", "_owner"]
    )

    assert evaluate_custody_role(
        rows
    )["security_status"] == (
        "RESTRICTED_CUSTODY_ROLE_VERIFIED"
    )


def test_wrong_database_role_still_blocked():
    rows = good_rows()
    rows[0]["runtime_role"] = "anastasia"

    result = evaluate_custody_role(rows)

    assert result["security_status"] == "BLOCKED"
    assert any(
        "runtime_role" in failure
        for failure in result["failures"]
    )
