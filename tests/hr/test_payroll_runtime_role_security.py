import pytest

from app.services.payroll_runtime_role_security import (
    RuntimeRoleSecurityError,
    evaluate_runtime_role,
)


def valid():
    return {
        "runtime_role": "erp_user",
        "table_owner": "erp_snapshot_owner",
        "role_is_superuser": False,
        "role_bypasses_rls": False,
        "role_is_owner": False,
        "role_inherits_owner": False,
        "role_member_of_owner": False,
        "can_select": True,
        "can_insert": True,
        "can_update": False,
        "can_delete": False,
        "can_truncate": False,
        "immutable_trigger_enabled": True,
    }


def test_restricted_role_passes():
    assert (
        evaluate_runtime_role(valid())["role_security"]
        == "RESTRICTED_ROLE_VERIFIED"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("runtime_role", "anastasia"),
        ("role_is_superuser", True),
        ("role_bypasses_rls", True),
        ("role_is_owner", True),
        ("role_inherits_owner", True),
        ("role_member_of_owner", True),
        ("can_update", True),
        ("can_delete", True),
        ("can_truncate", True),
        ("can_insert", False),
        ("immutable_trigger_enabled", False),
    ],
)
def test_privilege_escalation_fails_closed(field, value):
    row = valid()
    row[field] = value

    result = evaluate_runtime_role(row)

    assert result["role_security"] == "BLOCKED"
    assert result["official_generation"] == "BLOCKED"


def test_missing_evidence_rejected():
    with pytest.raises(RuntimeRoleSecurityError):
        evaluate_runtime_role(None)
