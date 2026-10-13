"""D1-B2: approval preflight is fail-closed and non-executing."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.payroll_deduction_approval_preflight_service as module
from app.services.payroll_deduction_evidence_policy import (
    PayrollDeductionEvidencePolicyError,
)
from app.services.payroll_deduction_trusted_approval_service import (
    TrustedApprovalResolutionError,
)

VALUES = dict(
    company_id=10,
    instruction_id=20,
    legal_category="enforcement_other",
    rule_version="v1",
    effective_on=date(2026, 10, 13),
)


def prepare(monkeypatch, *, category="enforcement_other", version="v1"):
    evidence = AsyncMock(
        return_value=SimpleNamespace(category=category)
    )

    approval = AsyncMock(
        return_value=SimpleNamespace(
            company_id=10,
            category=category,
            rule_version=version,
        )
    )

    monkeypatch.setattr(
        module,
        "resolve_verified_deduction_classification",
        evidence,
    )
    monkeypatch.setattr(
        module,
        "resolve_trusted_rule_approval",
        approval,
    )

    return evidence, approval


@pytest.mark.asyncio
async def test_matching_evidence_and_approval(monkeypatch):
    evidence, approval = prepare(monkeypatch)

    result = await module.verify_deduction_approval_preflight(
        object(),
        **VALUES,
    )

    assert result.company_id == 10
    assert result.instruction_id == 20
    assert result.rule_version == "v1"
    assert result.financial_execution_authorized is False

    assert evidence.await_args.kwargs["company_id"] == 10
    assert approval.await_args.kwargs["company_id"] == 10
    assert approval.await_args.kwargs["effective_on"] == VALUES["effective_on"]


@pytest.mark.asyncio
async def test_category_mismatch_blocks_approval_read(monkeypatch):
    _, approval = prepare(
        monkeypatch,
        category="enforcement_special",
    )

    with pytest.raises(module.DeductionApprovalPreflightError):
        await module.verify_deduction_approval_preflight(
            object(),
            **VALUES,
        )

    approval.assert_not_awaited()


@pytest.mark.asyncio
async def test_rule_version_mismatch_is_rejected(monkeypatch):
    prepare(monkeypatch, version="v2")

    with pytest.raises(module.DeductionApprovalPreflightError):
        await module.verify_deduction_approval_preflight(
            object(),
            **VALUES,
        )


@pytest.mark.asyncio
async def test_missing_verified_evidence_is_rejected(monkeypatch):
    evidence, approval = prepare(monkeypatch)
    evidence.side_effect = PayrollDeductionEvidencePolicyError(
        "Evidence missing"
    )

    with pytest.raises(module.DeductionApprovalPreflightError):
        await module.verify_deduction_approval_preflight(
            object(),
            **VALUES,
        )

    approval.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_persisted_approval_is_rejected(monkeypatch):
    _, approval = prepare(monkeypatch)
    approval.side_effect = TrustedApprovalResolutionError(
        "No approval"
    )

    with pytest.raises(module.DeductionApprovalPreflightError):
        await module.verify_deduction_approval_preflight(
            object(),
            **VALUES,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"company_id": 0},
        {"instruction_id": 0},
        {"rule_version": ""},
        {"legal_category": ""},
        {"effective_on": "2026-10-13"},
    ],
)
async def test_invalid_input_fails_before_db_reads(
    monkeypatch,
    change,
):
    evidence, approval = prepare(monkeypatch)

    values = {**VALUES, **change}

    with pytest.raises(module.DeductionApprovalPreflightError):
        await module.verify_deduction_approval_preflight(
            object(),
            **values,
        )

    evidence.assert_not_awaited()
    approval.assert_not_awaited()
