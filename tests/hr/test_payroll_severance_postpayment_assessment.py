from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.payroll_severance_postpayment_assessment import (
    SeveranceSettlementAssessmentError,
    classify_difference,
    money,
    assess_severance_postpayment,
)


@pytest.mark.parametrize(
    "latest,calculated,posted,expected,kind",
    [
        ("12000.00", "10000.00", 1, "2000.00",
         "POSTPAYMENT_ADDITIONAL_REVIEW"),
        ("8000.00", "10000.00", 1, "-2000.00",
         "POSTPAYMENT_OVERPAYMENT_REVIEW"),
        ("10000.00", "10000.00", 1, "0.00",
         "POSTED_AMOUNT_UNCHANGED"),
        ("12000.00", "10000.00", 0, "2000.00",
         "ADDITIONAL_ACCRUAL_REVIEW"),
        ("8000.00", "10000.00", 0, "-2000.00",
         "REDUCED_ACCRUAL_REVIEW"),
        ("0.00", "0.00", 0, "0.00",
         "NO_POSTED_PAYMENT"),
    ],
)
def test_difference_classification(
    latest, calculated, posted, expected, kind,
):
    delta, classification = classify_difference(
        latest=latest,
        calculated=calculated,
        posted_count=posted,
    )
    assert delta == Decimal(expected)
    assert classification == kind


@pytest.mark.parametrize(
    "value",
    ["NaN", "Infinity", "-Infinity", "1.001", True, "abc"],
)
def test_invalid_money_rejected(value):
    with pytest.raises(SeveranceSettlementAssessmentError):
        money(value)


@pytest.mark.parametrize(
    "company_id,payroll_input_id",
    [(0, 1), (1, 0), (True, 1), (1, False), (-1, 1)],
)
@pytest.mark.asyncio
async def test_invalid_scope_stops_before_database(
    company_id,
    payroll_input_id,
):
    db = MagicMock()
    db.scalars = AsyncMock()
    db.execute = AsyncMock()

    with pytest.raises(SeveranceSettlementAssessmentError):
        await assess_severance_postpayment(
            db,
            company_id=company_id,
            payroll_input_id=payroll_input_id,
        )

    db.scalars.assert_not_awaited()
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_verified_source_rejected():
    db = MagicMock()
    db.scalars = AsyncMock()
    db.scalars.return_value = MagicMock()
    db.scalars.return_value.all.return_value = []

    with pytest.raises(
        SeveranceSettlementAssessmentError,
        match="No verified",
    ):
        await assess_severance_postpayment(
            db,
            company_id=1,
            payroll_input_id=1,
        )


def test_assessment_is_not_payment_permission():
    from app.services.payroll_severance_postpayment_assessment import (
        SeveranceSettlementAssessment,
    )

    obj = SeveranceSettlementAssessment(
        company_id=1,
        payroll_input_id=2,
        latest_source_id=3,
        last_calculated_source_id=4,
        original_gross=Decimal("100.00"),
        latest_gross=Decimal("120.00"),
        gross_difference=Decimal("20.00"),
        posted_disbursement_count=1,
        posted_disbursement_total=Decimal("77.00"),
        classification="POSTPAYMENT_ADDITIONAL_REVIEW",
        review_required=True,
    )

    assert not obj.payment_authorized
    assert not obj.tax_correction_authorized
    assert not obj.journal_posting_authorized
