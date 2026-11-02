import pytest

from app.services.payroll_regulatory_persisted_correction_delta import (
    review_persisted_correction_delta,
)
from app.services.payroll_regulatory_financial_reconciliation import (
    FinancialReconciliationError,
)


@pytest.mark.asyncio
async def test_unverified_chain_is_blocked(monkeypatch):
    async def fake_chain(db, **kwargs):
        return {"status": "NOT_VERIFIED"}

    monkeypatch.setattr(
        "app.services.payroll_regulatory_persisted_correction_delta."
        "review_persisted_revision_chain",
        fake_chain,
    )

    result = await review_persisted_correction_delta(
        None,
        company_id=1,
        payroll_period_id=2,
    )

    assert result["status"] == "NOT_VERIFIED"
    assert result["persisted_correction_delta_verified"] is False
    assert result["official_generation"] == "BLOCKED"


@pytest.mark.asyncio
async def test_no_replacement_is_not_positive(monkeypatch):
    async def fake_chain(db, **kwargs):
        return {
            "status": "REVISION_CHAIN_VERIFIED",
            "active_calculation_ids": [10],
            "historical_calculation_ids": [],
        }

    monkeypatch.setattr(
        "app.services.payroll_regulatory_persisted_correction_delta."
        "review_persisted_revision_chain",
        fake_chain,
    )

    result = await review_persisted_correction_delta(
        None,
        company_id=1,
        payroll_period_id=2,
    )

    assert result["status"] == "NOT_VERIFIED"
    assert result["reason"] == (
        "NO_PERSISTED_REPLACEMENT_CORRECTION"
    )
    assert result["official_generation"] == "BLOCKED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "company_id,period_id",
    [
        (0, 2),
        (-1, 2),
        (1, 0),
        (1, -1),
        ("1", 2),
        (True, 2),
    ],
)
async def test_invalid_scope_rejected(company_id, period_id):
    with pytest.raises(FinancialReconciliationError):
        await review_persisted_correction_delta(
            None,
            company_id=company_id,
            payroll_period_id=period_id,
        )
