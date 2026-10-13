"""D3-B2-R2 orchestration tests: DB-backed calls, no posting."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.payroll_deduction_accounting_orchestrator as module

from app.services.payroll_deduction_batch_limit_preview import (
    BatchLimitLine,
    BatchLimitPreview,
)


D = Decimal
DAY = date(2026, 10, 13)


def request(
    instruction_id,
    priority,
    amount="40.00",
):
    return module.AccountingOrchestrationRequest(
        instruction_id=instruction_id,
        legal_category="enforcement_other",
        rule_version="test-version",
        priority=priority,
        requested_amount=D(amount),
    )


def arguments(**changes):
    result = dict(
        company_id=7,
        effective_on=DAY,
        statutory_net=D("100.00"),
        global_aggregate_cap=D("50.00"),
        requests=(
            request(12, 2),
            request(11, 1),
        ),
    )
    result.update(changes)
    return result


def prepare(monkeypatch):
    async def fake_d1(db, **kwargs):
        return SimpleNamespace(
            company_id=kwargs["company_id"],
            instruction_id=kwargs["instruction_id"],
            legal_category=kwargs["legal_category"],
            rule_version=kwargs["rule_version"],
            effective_on=kwargs["effective_on"],
            financial_execution_authorized=False,
        )

    async def fake_d2(db, **kwargs):
        ordered = kwargs["requests"]

        assert [item.instruction_id for item in ordered] == [
            11, 12,
        ]

        return BatchLimitPreview(
            lines=(
                BatchLimitLine(
                    instruction_id=11,
                    priority=1,
                    requested_amount=D("40.00"),
                    permitted_amount=D("40.00"),
                    unapplied_amount=D("0.00"),
                ),
                BatchLimitLine(
                    instruction_id=12,
                    priority=2,
                    requested_amount=D("40.00"),
                    permitted_amount=D("10.00"),
                    unapplied_amount=D("30.00"),
                ),
            ),
            total_requested=D("80.00"),
            total_permitted=D("50.00"),
            total_unapplied=D("30.00"),
            remaining_global_capacity=D("0.00"),
        )

    d1 = AsyncMock(side_effect=fake_d1)
    d2 = AsyncMock(side_effect=fake_d2)

    monkeypatch.setattr(
        module,
        "verify_deduction_approval_preflight",
        d1,
    )
    monkeypatch.setattr(
        module,
        "preview_payroll_deduction_batch_limits",
        d2,
    )

    return d1, d2


@pytest.mark.asyncio
async def test_trusted_orchestration_remains_non_posting(
    monkeypatch,
):
    d1, d2 = prepare(monkeypatch)
    db = object()

    result = await module.evaluate_trusted_accounting_orchestration(
        db,
        **arguments(),
    )

    assert len(result.accounting_preflights) == 2

    assert [
        row.instruction_id
        for row in result.accounting_preflights
    ] == [11, 12]

    assert [
        row.candidate_permitted_amount
        for row in result.accounting_preflights
    ] == [D("40.00"), D("10.00")]

    assert result.financial_execution_authorized is False
    assert result.journal_posting_allowed is False
    assert result.legal_authorization == "NOT_AUTHORIZED"

    assert d1.await_count == 2
    d2.assert_awaited_once()

    assert all(
        call.args[0] is db
        for call in d1.await_args_list
    )

    assert d2.await_args.args[0] is db


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"company_id": 0},
        {"effective_on": "2026-10-13"},
        {"statutory_net": D("-1.00")},
        {"global_aggregate_cap": D("120.00")},
        {"requests": ()},
        {"requests": (request(11, 1), request(11, 2))},
        {"requests": (request(11, 1), request(12, 1))},
        {"requests": (request(11, 1, "1.001"),)},
    ],
)
async def test_invalid_inputs_fail_before_db(
    monkeypatch,
    changes,
):
    d1, d2 = prepare(monkeypatch)

    with pytest.raises(module.AccountingOrchestrationError):
        await module.evaluate_trusted_accounting_orchestration(
            object(),
            **arguments(**changes),
        )

    d1.assert_not_awaited()
    d2.assert_not_awaited()


@pytest.mark.asyncio
async def test_d1_identity_mismatch_blocks_d2(monkeypatch):
    d1, d2 = prepare(monkeypatch)

    async def wrong_company(db, **kwargs):
        return SimpleNamespace(
            company_id=999,
            instruction_id=kwargs["instruction_id"],
            legal_category=kwargs["legal_category"],
            rule_version=kwargs["rule_version"],
            effective_on=kwargs["effective_on"],
            financial_execution_authorized=False,
        )

    d1.side_effect = wrong_company

    with pytest.raises(module.AccountingOrchestrationError):
        await module.evaluate_trusted_accounting_orchestration(
            object(),
            **arguments(),
        )

    d2.assert_not_awaited()


@pytest.mark.asyncio
async def test_d1_authorization_flag_rejected(monkeypatch):
    d1, d2 = prepare(monkeypatch)

    async def unsafe_d1(db, **kwargs):
        return SimpleNamespace(
            **kwargs,
            financial_execution_authorized=True,
        )

    d1.side_effect = unsafe_d1

    with pytest.raises(module.AccountingOrchestrationError):
        await module.evaluate_trusted_accounting_orchestration(
            object(),
            **arguments(),
        )

    d2.assert_not_awaited()


@pytest.mark.asyncio
async def test_d2_authorization_flag_rejected(monkeypatch):
    d1, d2 = prepare(monkeypatch)

    valid = await module.evaluate_trusted_accounting_orchestration(
        object(),
        **arguments(),
    )

    d2.reset_mock()

    d2.return_value = replace(
        valid.batch_preview,
        financial_execution_authorized=True,
    )
    d2.side_effect = None

    with pytest.raises(module.AccountingOrchestrationError):
        await module.evaluate_trusted_accounting_orchestration(
            object(),
            **arguments(),
        )


@pytest.mark.asyncio
async def test_d2_wrong_instruction_rejected(monkeypatch):
    d1, d2 = prepare(monkeypatch)

    valid = await module.evaluate_trusted_accounting_orchestration(
        object(),
        **arguments(),
    )

    wrong = replace(
        valid.batch_preview.lines[1],
        instruction_id=99,
    )

    d2.return_value = replace(
        valid.batch_preview,
        lines=(valid.batch_preview.lines[0], wrong),
    )
    d2.side_effect = None

    with pytest.raises(module.AccountingOrchestrationError):
        await module.evaluate_trusted_accounting_orchestration(
            object(),
            **arguments(),
        )
