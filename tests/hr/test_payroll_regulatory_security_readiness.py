from copy import deepcopy
import pytest

from app.services.payroll_regulatory_security_readiness import (
    evaluate_security_evidence,
    regulatory_readiness,
)


def safe_evidence():
    return {
        "snapshot_table_exists": True,
        "trigger_enabled": True,
        "runtime_role": "erp_runtime",
        "table_owner": "erp_migrator",
        "role_is_superuser": False,
        "role_is_table_owner": False,
        "role_can_inherit_owner": False,
        "can_insert": True,
        "can_update": False,
        "can_delete": False,
        "can_truncate": False,
    }


def test_separated_role_evidence():
    result = evaluate_security_evidence(safe_evidence())
    assert result["security_status"] == "ROLE_SEPARATION_VERIFIED"
    assert result["failures"] == []


@pytest.mark.parametrize("field,value", [
    ("snapshot_table_exists", False),
    ("trigger_enabled", False),
    ("role_is_superuser", True),
    ("role_is_table_owner", True),
    ("role_can_inherit_owner", True),
    ("can_insert", False),
    ("can_update", True),
    ("can_delete", True),
    ("can_truncate", True),
    ("trigger_enabled", None),
    ("role_is_table_owner", None),
])
def test_fail_closed_database_security(field, value):
    evidence = deepcopy(safe_evidence())
    evidence[field] = value
    result = evaluate_security_evidence(evidence)
    assert result["security_status"] == "BLOCKED"
    assert result["failures"]
    assert result["official_generation"] == "BLOCKED"


@pytest.mark.parametrize("report", ("d1", "4df", "d5", "d6"))
def test_all_reports_remain_blocked(report):
    security = evaluate_security_evidence(safe_evidence())
    result = regulatory_readiness(
        security=security,
        source_status={report: "APPROVED"},
    )
    assert result["reports"][report]["official_generation"] == "BLOCKED"
    assert result["reports"][report]["blockers"]
    assert result["all_reports_officially_ready"] is False


def test_missing_security_is_blocked():
    result = regulatory_readiness(security=None)
    assert result["official_generation"] == "BLOCKED"
    for report in ("d1", "4df", "d5", "d6"):
        assert result["reports"][report]["blockers"]
