from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.payroll_severance_postpayment_funding import (
    PostedPayrollFunding,
    SeveranceFundingEvidenceError,
    validate_funding_evidence,
    load_posted_payroll_funding,
)


def evidence():
    return PostedPayrollFunding(
        company_id=1,
        payroll_calculation_id=2,
        as_of=date(2026, 10, 30),
        currency_code="UAH",
        posted_payment_count=1,
        posted_employee_amount=D("10040.80"),
        posted_withheld_amount=D("0.00"),
        posted_payment_total=D("10040.80"),
        remitted_personal_income_tax=D("2347.20"),
        remitted_military_levy=D("652.00"),
        remitted_employer_contribution=D("228.80"),
    )


def test_posted_funding_reconciles():
    result = validate_funding_evidence(evidence())

    assert result.posted_payment_total == D("10040.80")
    assert not result.financial_execution_authorized
    assert not result.severance_tax_attribution_verified
    assert not result.advance_tax_reconciliation_complete


def test_payment_tampering_rejected():
    with pytest.raises(
        SeveranceFundingEvidenceError,
        match="do not reconcile",
    ):
        validate_funding_evidence(
            replace(
                evidence(),
                posted_payment_total=D("10040.81"),
            )
        )


def test_permission_forgery_rejected():
    with pytest.raises(
        SeveranceFundingEvidenceError,
        match="cannot grant",
    ):
        validate_funding_evidence(
            replace(
                evidence(),
                financial_execution_authorized=True,
            )
        )


@pytest.mark.parametrize(
    "field,bad",
    [
        ("posted_employee_amount", D("-1.00")),
        ("posted_withheld_amount", D("NaN")),
        ("remitted_personal_income_tax", D("1.001")),
        ("remitted_military_levy", D("Infinity")),
    ],
)
def test_invalid_money_rejected(field, bad):
    with pytest.raises(SeveranceFundingEvidenceError):
        validate_funding_evidence(
            replace(evidence(), **{field: bad})
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "company_id,calculation_id,as_of",
    [
        (0, 1, date(2026, 10, 30)),
        (1, 0, date(2026, 10, 30)),
        (True, 1, date(2026, 10, 30)),
        (1, 1, "2026-10-30"),
    ],
)
async def test_invalid_identity_stops_before_db(
    company_id, calculation_id, as_of
):
    db = MagicMock()
    db.scalar = AsyncMock()

    with pytest.raises(SeveranceFundingEvidenceError):
        await load_posted_payroll_funding(
            db,
            company_id=company_id,
            payroll_calculation_id=calculation_id,
            as_of=as_of,
        )

    db.scalar.assert_not_awaited()
