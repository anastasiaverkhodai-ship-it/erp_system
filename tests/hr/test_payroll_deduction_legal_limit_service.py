"""Integration contract for verified evidence and candidate limits."""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.services.payroll_deduction_evidence_policy import (
    PayrollDeductionEvidencePolicyError,
)
from app.services.payroll_deduction_legal_classification import (
    CATEGORIES,
)
from app.services.payroll_deduction_legal_limit_service import (
    evaluate_verified_deduction_limit,
)


@pytest.mark.asyncio
async def test_verified_evidence_limits_requested_amount():
    with patch(
        "app.services.payroll_deduction_legal_limit_service."
        "resolve_verified_deduction_classification",
        new_callable=AsyncMock,
        return_value=CATEGORIES["enforcement_other"],
    ) as resolver:
        result = await evaluate_verified_deduction_limit(
            AsyncMock(),
            company_id=7,
            payroll_deduction_instruction_id=10,
            statutory_net=Decimal("10000.00"),
            requested_amount=Decimal("4000.00"),
            already_withheld=Decimal("0.00"),
        )

    assert result.permitted_amount == Decimal("2000.00")
    assert result.unapplied_amount == Decimal("2000.00")

    resolver.assert_awaited_once()


@pytest.mark.asyncio
async def test_aggregate_remaining_is_enforced():
    with patch(
        "app.services.payroll_deduction_legal_limit_service."
        "resolve_verified_deduction_classification",
        new_callable=AsyncMock,
        return_value=CATEGORIES["enforcement_other"],
    ):
        result = await evaluate_verified_deduction_limit(
            AsyncMock(),
            company_id=7,
            payroll_deduction_instruction_id=10,
            statutory_net=Decimal("10000.00"),
            requested_amount=Decimal("2000.00"),
            already_withheld=Decimal("4500.00"),
        )

    assert result.permitted_amount == Decimal("500.00")
    assert result.unapplied_amount == Decimal("1500.00")


@pytest.mark.asyncio
async def test_missing_verified_evidence_fails_closed():
    with patch(
        "app.services.payroll_deduction_legal_limit_service."
        "resolve_verified_deduction_classification",
        new_callable=AsyncMock,
        side_effect=PayrollDeductionEvidencePolicyError(
            "Verified legal evidence is required"
        ),
    ):
        with pytest.raises(PayrollDeductionEvidencePolicyError):
            await evaluate_verified_deduction_limit(
                AsyncMock(),
                company_id=7,
                payroll_deduction_instruction_id=10,
                statutory_net=Decimal("10000.00"),
                requested_amount=Decimal("100.00"),
                already_withheld=Decimal("0.00"),
            )


@pytest.mark.asyncio
async def test_legal_limit_service_does_not_commit():
    db = AsyncMock()

    with patch(
        "app.services.payroll_deduction_legal_limit_service."
        "resolve_verified_deduction_classification",
        new_callable=AsyncMock,
        return_value=CATEGORIES["minor_child_alimony"],
    ):
        result = await evaluate_verified_deduction_limit(
            db,
            company_id=7,
            payroll_deduction_instruction_id=10,
            statutory_net=Decimal("10000.00"),
            requested_amount=Decimal("9000.00"),
            already_withheld=Decimal("0.00"),
        )

    assert result.permitted_amount == Decimal("7000.00")

    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()
    db.flush.assert_not_awaited()
