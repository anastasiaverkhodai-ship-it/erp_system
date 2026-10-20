from datetime import date
from types import SimpleNamespace

import pytest

from app.services.payroll_termination_severance_preflight import (
    SeverancePreflightError,
    preflight_termination_severance,
)


class FakeDB:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.read_count = 0

    async def scalar(self, query):
        self.read_count += 1
        return self.responses.pop(0)


def fixtures(ground="redundancy", provision="40.1"):
    termination = date(2026, 10, 20)

    payroll_input = SimpleNamespace(
        id=10,
        company_id=5,
        payroll_period_id=20,
        employment_contract_id=30,
    )

    period = SimpleNamespace(
        id=20,
        company_id=5,
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 31),
        status="draft",
    )

    contract = SimpleNamespace(
        id=30,
        company_id=5,
        status="ended",
        end_date=termination,
    )

    event = SimpleNamespace(
        id=40,
        company_id=5,
        employment_contract_id=30,
        event_type="termination",
        effective_date=termination,
        order_number="ORDER-2026-001",
        request_payload={
            "termination_basis": {
                "ground": ground,
                "labour_code_provision": provision,
                "source_reference": "Verified dismissal order",
            }
        },
    )

    return payroll_input, period, contract, event


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ground,provision,category,multiplier",
    [
        ("redundancy", "40.1", "average_months", 1),
        ("military_service", "36.3", "minimum_wages", 2),
        ("other", "41.5", "average_months", 6),
        ("other", "38", "manual_review", None),
    ],
)
async def test_documented_termination_is_linked(
    ground, provision, category, multiplier
):
    rows = fixtures(ground, provision)
    db = FakeDB(*rows, None)

    result = await preflight_termination_severance(
        db,
        company_id=5,
        payroll_input_id=10,
    )

    assert result.company_id == 5
    assert result.employment_contract_id == 30
    assert result.termination_event_id == 40
    assert result.termination_date == date(2026, 10, 20)
    assert result.termination_order_number == "ORDER-2026-001"
    assert result.policy.category == category
    assert result.policy.multiplier == multiplier
    assert db.read_count == 5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "index,field,value,error",
    [
        (0, "company_id", 6, "Company payroll input"),
        (1, "status", "finalized", "open company"),
        (2, "status", "active", "Ended employment"),
        (
            2, "end_date", date(2026, 11, 1),
            "within the payroll period",
        ),
        (
            3, "employment_contract_id", 99,
            "Active documented termination",
        ),
    ],
)
async def test_mismatched_source_rejected(
    index, field, value, error
):
    rows = list(fixtures())
    setattr(rows[index], field, value)

    db = FakeDB(*rows, None)

    with pytest.raises(SeverancePreflightError, match=error):
        await preflight_termination_severance(
            db,
            company_id=5,
            payroll_input_id=10,
        )


@pytest.mark.asyncio
async def test_reversed_termination_rejected():
    db = FakeDB(*fixtures(), 900)

    with pytest.raises(
        SeverancePreflightError,
        match="Reversed termination",
    ):
        await preflight_termination_severance(
            db, company_id=5, payroll_input_id=10
        )


@pytest.mark.asyncio
async def test_invalid_provision_rejected():
    rows = list(fixtures())
    rows[3].request_payload["termination_basis"][
        "labour_code_provision"
    ] = "36.1"

    db = FakeDB(*rows, None)

    with pytest.raises(
        SeverancePreflightError,
        match="Invalid documented",
    ):
        await preflight_termination_severance(
            db, company_id=5, payroll_input_id=10
        )


@pytest.mark.asyncio
async def test_missing_basis_never_creates_automatic_entitlement():
    rows = list(fixtures())
    rows[3].request_payload = {}

    db = FakeDB(*rows, None)

    result = await preflight_termination_severance(
        db, company_id=5, payroll_input_id=10
    )

    assert result.policy.category == "manual_review"
    assert result.policy.multiplier is None


@pytest.mark.asyncio
async def test_missing_input_rejected():
    db = FakeDB(None)

    with pytest.raises(
        SeverancePreflightError,
        match="Company payroll input",
    ):
        await preflight_termination_severance(
            db, company_id=5, payroll_input_id=10
        )
