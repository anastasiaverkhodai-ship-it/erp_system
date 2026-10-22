"""Trusted classification reaches taxes; arbitrary snapshot evidence cannot."""
import pytest
from test_payroll_severance_snapshot_service_postgresql import _run_severance_snapshot_service_e2e

@pytest.mark.asyncio
@pytest.mark.parametrize("trusted_policy", [False, True])
async def test_persisted_severance_snapshot_financial_integration(trusted_policy):
    await _run_severance_snapshot_service_e2e(
        exercise_financial_path=True, trusted_policy=trusted_policy,
    )
