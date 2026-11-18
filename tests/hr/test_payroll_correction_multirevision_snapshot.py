"""Versioned predecessor snapshots retain the full chain and v1 guard."""
from copy import deepcopy
import pytest
from test_payroll_regulatory_correction_projection import sample
from app.services.payroll_regulatory_correction_projection import build_correction_projection, seal_projection
from app.services.payroll_regulatory_correction_snapshot_store import validate_snapshot_envelope, CorrectionSnapshotError
from app.services.payroll_regulatory_financial_reconciliation import FinancialReconciliationError


def projection():
    control, delta = sample()
    control["excluded_historical_calculation_ids"] = [10, 11]
    delta["historical_calculation_ids"] = [10, 11]
    delta["baseline_calculation_ids"] = [11]
    return build_correction_projection(control, delta)


def check(payload):
    return validate_snapshot_envelope(company_id=1, payroll_period_id=2,
        report_kind="d1", sealed=seal_projection(payload))


def test_v2_keys_use_predecessor_and_keep_full_history():
    payload = projection()
    assert payload["excluded_historical_calculation_ids"] == [10, 11]
    result = check(payload)
    assert result["original_calculation_id"] == 11
    assert result["replacement_calculation_id"] == 12
    assert result["schema_version"] == "payroll_internal_correction_projection_v2"


@pytest.mark.parametrize("baseline", [None, [], [10, 11], [11, 11], [99], [True]])
def test_projection_rejects_unsafe_baseline(baseline):
    control, delta = sample()
    control["excluded_historical_calculation_ids"] = [10, 11]
    delta["historical_calculation_ids"] = [10, 11]
    delta["baseline_calculation_ids"] = baseline
    with pytest.raises(FinancialReconciliationError, match="baseline"):
        build_correction_projection(control, delta)


@pytest.mark.parametrize("key,value", [
    ("baseline_calculation_ids", []),
    ("baseline_calculation_ids", [99]),
    ("baseline_calculation_ids", [10, 11]),
    ("baseline_semantics", "accumulated_history"),
    ("excluded_historical_calculation_ids", [11]),
    ("excluded_historical_calculation_ids", [11, 11]),
    ("active_calculation_ids", [11]),
    ("active_calculation_ids", [12, 13]),
    ("schema", "payroll_internal_correction_projection_v1"),
])
def test_even_resealed_invalid_envelope_is_rejected(key, value):
    payload = deepcopy(projection())
    payload[key] = value
    with pytest.raises(CorrectionSnapshotError):
        check(payload)
