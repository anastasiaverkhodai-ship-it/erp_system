"""Tests for read-only controlled payroll deduction previews."""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.services.payroll_deduction_controlled_preview import (
    PayrollDeductionPreviewError,
    preview_payroll_deduction,
)
from app.services.payroll_deduction_verified_limit import (
    VerifiedDeductionLimit,
)


BASE = {
    "company_id": 1,
    "payroll_deduction_instruction_id": 10,
    "statutory_net": Decimal("10000.00"),
    "requested_amount": Decimal("3000.00"),
    "already_withheld": Decimal("1000.00"),
}


def expected_limit():
    return VerifiedDeductionLimit(
        category="enforcement_other",
        requested_amount=Decimal("3000.00"),
        permitted_amount=Decimal("2000.00"),
        unapplied_amount=Decimal("1000.00"),
        individual_maximum=Decimal("2000.00"),
        aggregate_maximum=Decimal("5000.00"),
        aggregate_remaining=Decimal("4000.00"),
    )


@pytest.mark.asyncio
async def test_preview_returns_candidate_amounts():
    db = AsyncMock()

    with patch(
        "app.services.payroll_deduction_controlled_preview."
        "evaluate_verified_deduction_limit",
        new_callable=AsyncMock,
        return_value=expected_limit(),
    ) as evaluator:

        result = await preview_payroll_deduction(
            db,
            **BASE,
        )

    assert result.category == "enforcement_other"
    assert result.permitted_amount == Decimal("2000.00")
    assert result.unapplied_amount == Decimal("1000.00")
    assert result.automatic_application_allowed is False

    evaluator.assert_awaited_once()

    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "company_id",
    [0, -1, None, True, "1"],
)
async def test_invalid_company_is_rejected(company_id):
    db = AsyncMock()

    with pytest.raises(PayrollDeductionPreviewError):
        await preview_payroll_deduction(
            db,
            **{**BASE, "company_id": company_id},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "instruction_id",
    [0, -1, None, True, "10"],
)
async def test_invalid_instruction_is_rejected(instruction_id):
    db = AsyncMock()

    with pytest.raises(PayrollDeductionPreviewError):
        await preview_payroll_deduction(
            db,
            **{
                **BASE,
                "payroll_deduction_instruction_id": instruction_id,
            },
        )


@pytest.mark.asyncio
async def test_preview_never_changes_payroll():
    db = AsyncMock()

    with patch(
        "app.services.payroll_deduction_controlled_preview."
        "evaluate_verified_deduction_limit",
        new_callable=AsyncMock,
        return_value=expected_limit(),
    ):
        await preview_payroll_deduction(db, **BASE)

    db.add.assert_not_called()
    db.add_all.assert_not_called()
    db.delete.assert_not_awaited()
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()
    db.flush.assert_not_awaited()
