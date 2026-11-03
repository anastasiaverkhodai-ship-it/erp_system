from decimal import Decimal
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import pytest

from app.services.payroll_severance_corrected_tax_evidence import (
    CorrectedSeveranceTaxEvidenceError,
    verify_corrected_severance_tax_evidence,
    verify_corrected_tax_amounts,
)
from app.services.payroll_severance_net_tax_settlement import (
    SeveranceFinancialSnapshot,
)


def financial():
    return SeveranceFinancialSnapshot(
        gross=Decimal("1200.00"),
        personal_income_tax=Decimal("216.00"),
        military_levy=Decimal("60.00"),
        employer_contribution=Decimal("0.00"),
        net=Decimal("924.00"),
    )


def proposal():
    return Obj(
        proposed_financial={
            "gross": "1200.00",
            "personal_income_tax": "216.00",
            "military_levy": "60.00",
            "employer_contribution": "0.00",
            "net": "924.00",
        }
    )


def test_exact_calculated_amounts_match_proposal():
    result = verify_corrected_tax_amounts(
        proposal=proposal(),
        corrected_financial=financial(),
        corrected_gross=Decimal("1200.00"),
    )
    assert result["net"] == Decimal("924.00")
    assert result["employer_contribution"] == Decimal("0.00")


@pytest.mark.parametrize(
    "field,value",
    [
        ("gross", "1201.00"),
        ("personal_income_tax", "215.00"),
        ("military_levy", "59.00"),
        ("employer_contribution", "1.00"),
        ("net", "923.00"),
    ],
)
def test_proposal_component_mismatch_rejected(field, value):
    row = proposal()
    row.proposed_financial[field] = value

    with pytest.raises(CorrectedSeveranceTaxEvidenceError):
        verify_corrected_tax_amounts(
            proposal=row,
            corrected_financial=financial(),
            corrected_gross=Decimal("1200.00"),
        )


@pytest.mark.parametrize(
    "gross",
    ["NaN", "Infinity", "1200.001", "-1", "0", "1199.00"],
)
def test_corrected_source_gross_mismatch_rejected(gross):
    with pytest.raises(CorrectedSeveranceTaxEvidenceError):
        verify_corrected_tax_amounts(
            proposal=proposal(),
            corrected_financial=financial(),
            corrected_gross=gross,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "company_id,proposal_id,calculation_id",
    [
        (0, 20, 30),
        (1, 0, 30),
        (1, 20, 0),
        (True, 20, 30),
        (1, True, 30),
        (1, 20, True),
    ],
)
async def test_invalid_ids_rejected_before_queries(
    company_id,
    proposal_id,
    calculation_id,
):
    db = Obj(scalar=AsyncMock())

    with pytest.raises(CorrectedSeveranceTaxEvidenceError):
        await verify_corrected_severance_tax_evidence(
            db,
            company_id=company_id,
            financial_proposal_id=proposal_id,
            corrected_calculation_id=calculation_id,
        )

    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_original_payroll_reuse_rejected():
    db = Obj(
        scalar=AsyncMock(
            side_effect=[
                Obj(
                    id=20,
                    company_id=1,
                    correction_case_id=10,
                    status="review_required",
                ),
                Obj(
                    id=10,
                    company_id=1,
                    original_payroll_calculation_id=30,
                    status="review_required",
                ),
            ]
        )
    )

    with pytest.raises(
        CorrectedSeveranceTaxEvidenceError,
        match="Historical original payroll",
    ):
        await verify_corrected_severance_tax_evidence(
            db,
            company_id=1,
            financial_proposal_id=20,
            corrected_calculation_id=30,
        )


@pytest.mark.asyncio
async def test_missing_proposal_rejected():
    db = Obj(scalar=AsyncMock(return_value=None))

    with pytest.raises(
        CorrectedSeveranceTaxEvidenceError,
        match="Immutable financial proposal",
    ):
        await verify_corrected_severance_tax_evidence(
            db,
            company_id=1,
            financial_proposal_id=20,
            corrected_calculation_id=31,
        )


def test_corrected_calculation_must_be_newer_same_scope():
    from app.services.payroll_severance_corrected_tax_evidence import (
        validate_corrected_calculation_lineage,
    )

    original = Obj(
        id=30, company_id=1, payroll_input_id=10,
        payroll_period_id=4, employment_contract_id=6,
        revision=1, currency_code="UAH",
    )
    corrected = Obj(
        id=31, company_id=1, payroll_input_id=10,
        payroll_period_id=4, employment_contract_id=6,
        revision=2, currency_code="UAH",
    )

    assert validate_corrected_calculation_lineage(
        original=original, corrected=corrected,
    )

    for field, invalid in (
        ("revision", 1),
        ("revision", 0),
        ("revision", True),
        ("company_id", 2),
        ("payroll_input_id", 11),
        ("payroll_period_id", 5),
        ("employment_contract_id", 7),
        ("currency_code", "EUR"),
        ("id", 30),
    ):
        bad = Obj(**vars(corrected))
        setattr(bad, field, invalid)
        with pytest.raises(CorrectedSeveranceTaxEvidenceError):
            validate_corrected_calculation_lineage(
                original=original, corrected=bad,
            )


def test_corrected_calculation_requires_original():
    from app.services.payroll_severance_corrected_tax_evidence import (
        validate_corrected_calculation_lineage,
    )
    with pytest.raises(CorrectedSeveranceTaxEvidenceError):
        validate_corrected_calculation_lineage(
            original=None, corrected=Obj(),
        )
