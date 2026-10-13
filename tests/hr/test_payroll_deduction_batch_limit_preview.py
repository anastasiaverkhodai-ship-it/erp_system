"""D2-B: candidate batch limits remain conserved and non-posting."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.payroll_deduction_batch_limit_preview as module

D = Decimal


def request(identifier, priority, amount):
    return module.BatchLimitRequest(
        instruction_id=identifier,
        priority=priority,
        requested_amount=D(amount),
    )


def fake_limits(monkeypatch):
    async def calculate(db, **kwargs):
        # Fake an existing verified limit result; real evidence
        # resolution remains covered by its own service tests.
        return SimpleNamespace(
            permitted_amount=min(
                kwargs["requested_amount"],
                D("40.00"),
            )
        )

    mocked = AsyncMock(side_effect=calculate)

    monkeypatch.setattr(
        module,
        "evaluate_verified_deduction_limit",
        mocked,
    )

    return mocked


@pytest.mark.asyncio
async def test_batch_respects_priority_and_global_cap(monkeypatch):
    mocked = fake_limits(monkeypatch)

    result = await module.preview_payroll_deduction_batch_limits(
        object(),
        company_id=10,
        statutory_net=D("100.00"),
        global_aggregate_cap=D("50.00"),
        requests=(
            request(2, 2, "40.00"),
            request(1, 1, "40.00"),
        ),
    )

    assert [line.instruction_id for line in result.lines] == [1, 2]
    assert [line.permitted_amount for line in result.lines] == [
        D("40.00"),
        D("10.00"),
    ]
    assert result.total_requested == D("80.00")
    assert result.total_permitted == D("50.00")
    assert result.total_unapplied == D("30.00")
    assert result.remaining_global_capacity == D("0.00")
    assert result.financial_execution_authorized is False
    assert result.legal_authorization == "NOT_AUTHORIZED"

    assert mocked.await_count == 2
    assert mocked.await_args_list[1].kwargs["already_withheld"] == D("40.00")


@pytest.mark.asyncio
async def test_zero_global_cap_preserves_full_remainder(monkeypatch):
    fake_limits(monkeypatch)

    result = await module.preview_payroll_deduction_batch_limits(
        object(),
        company_id=10,
        statutory_net=D("100.00"),
        global_aggregate_cap=D("0.00"),
        requests=(request(1, 1, "30.00"),),
    )

    assert result.total_permitted == D("0.00")
    assert result.total_unapplied == D("30.00")


@pytest.mark.asyncio
async def test_empty_batch_is_safe():
    result = await module.preview_payroll_deduction_batch_limits(
        object(),
        company_id=10,
        statutory_net=D("100.00"),
        global_aggregate_cap=D("50.00"),
        requests=(),
    )

    assert result.lines == ()
    assert result.total_permitted == D("0.00")
    assert result.financial_execution_authorized is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requests",
    [
        (request(1, 1, "10.00"), request(1, 2, "10.00")),
        (request(1, 1, "10.00"), request(2, 1, "10.00")),
        (request(1, 1, "-1.00"),),
        (request(1, 1, "1.001"),),
    ],
)
async def test_bad_batch_fails_before_db_read(monkeypatch, requests):
    mocked = fake_limits(monkeypatch)

    with pytest.raises(module.BatchLimitPreviewError):
        await module.preview_payroll_deduction_batch_limits(
            object(),
            company_id=10,
            statutory_net=D("100.00"),
            global_aggregate_cap=D("50.00"),
            requests=requests,
        )

    mocked.assert_not_awaited()


@pytest.mark.asyncio
async def test_overstated_verified_result_is_rejected(monkeypatch):
    mocked = AsyncMock(
        return_value=SimpleNamespace(
            permitted_amount=D("200.00")
        )
    )
    monkeypatch.setattr(
        module,
        "evaluate_verified_deduction_limit",
        mocked,
    )

    with pytest.raises(module.BatchLimitPreviewError):
        await module.preview_payroll_deduction_batch_limits(
            object(),
            company_id=10,
            statutory_net=D("100.00"),
            global_aggregate_cap=D("50.00"),
            requests=(request(1, 1, "20.00"),),
        )


@pytest.mark.asyncio
async def test_global_cap_above_net_is_rejected(monkeypatch):
    mocked = fake_limits(monkeypatch)

    with pytest.raises(module.BatchLimitPreviewError):
        await module.preview_payroll_deduction_batch_limits(
            object(),
            company_id=10,
            statutory_net=D("100.00"),
            global_aggregate_cap=D("120.00"),
            requests=(request(1, 1, "20.00"),),
        )

    mocked.assert_not_awaited()
