"""Read-only three-component orchestration contracts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import app.services.payroll_dated_statutory_verified_plan as module


class FakeSession:
    def __init__(self, *, calculation=True, peer=False, contract=True):
        self.calls = 0
        self.calculation = (
            SimpleNamespace(
                id=12,
                company_id=7,
                payroll_period_id=9,
                employment_contract_id=4,
                currency_code="UAH",
                gross_amount="10000.00",
            )
            if calculation else None
        )
        self.contract = (
            SimpleNamespace(id=4, employee_id=10)
            if contract else None
        )
        self.peer = peer

    async def scalar(self, statement):
        self.calls += 1
        if self.calls == 1:
            return self.calculation
        if self.calls == 2:
            return self.contract
        raise AssertionError("Unexpected scalar query")

    async def scalars(self, statement):
        return SimpleNamespace(all=lambda: [99] if self.peer else [])

    async def flush(self):
        raise AssertionError("Read-only planner must not flush")

    async def commit(self):
        raise AssertionError("Read-only planner must not commit")


@pytest.mark.asyncio
async def test_three_verified_components_precede_plan(monkeypatch):
    db = FakeSession()
    seen = []

    async def preview(db, *, company_id, payroll_calculation_id, component):
        seen.append(component.value)
        return (component.value,)

    sentinel = object()
    build = Mock(return_value=sentinel)

    monkeypatch.setattr(
        module, "preview_verified_dated_salary_tax", preview
    )
    monkeypatch.setattr(
        module, "build_dated_statutory_plan", build
    )

    outcome = await module.build_verified_dated_salary_statutory_plan(
        db, company_id=7, payroll_calculation_id=12
    )

    assert outcome is sentinel
    assert seen == [item.value for item in module.COMPONENTS]
    assert set(build.call_args.kwargs["components"]) == set(seen)
    print("F2-C1 THREE VERIFIED COMPONENTS = PASS")
    print("F2-C1 ZERO DATABASE WRITES = PASS")


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [0, -1, True, "7", None])
async def test_invalid_company_rejected(value):
    with pytest.raises(module.VerifiedDatedStatutoryPlanError):
        await module.build_verified_dated_salary_statutory_plan(
            FakeSession(), company_id=value, payroll_calculation_id=12
        )


@pytest.mark.asyncio
async def test_missing_calculation_rejected():
    with pytest.raises(
        module.VerifiedDatedStatutoryPlanError,
        match="not found",
    ):
        await module.build_verified_dated_salary_statutory_plan(
            FakeSession(calculation=False),
            company_id=7,
            payroll_calculation_id=12,
        )


@pytest.mark.asyncio
async def test_employee_peer_rejected(monkeypatch):
    preview = AsyncMock()
    monkeypatch.setattr(
        module, "preview_verified_dated_salary_tax", preview
    )

    with pytest.raises(
        module.VerifiedDatedStatutoryPlanError,
        match="employee-wide",
    ):
        await module.build_verified_dated_salary_statutory_plan(
            FakeSession(peer=True),
            company_id=7,
            payroll_calculation_id=12,
        )

    preview.assert_not_awaited()
    print("F2-C1 EMPLOYEE-WIDE GUARD = PASS")


@pytest.mark.asyncio
async def test_any_component_failure_blocks_plan(monkeypatch):
    preview = AsyncMock(side_effect=ValueError("Invalid rate provenance"))
    build = Mock()

    monkeypatch.setattr(
        module, "preview_verified_dated_salary_tax", preview
    )
    monkeypatch.setattr(
        module, "build_dated_statutory_plan", build
    )

    with pytest.raises(ValueError, match="Invalid rate provenance"):
        await module.build_verified_dated_salary_statutory_plan(
            FakeSession(),
            company_id=7,
            payroll_calculation_id=12,
        )

    build.assert_not_called()
    print("F2-C1 COMPONENT FAILURE BLOCKS PLAN = PASS")
