from decimal import Decimal
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import pytest

from app.services.payroll_severance_postpaid_correction_package import (
    SeverancePostpaidPackageError,
    build_severance_postpaid_correction_package,
    load_severance_postpaid_correction_package,
)


D = Decimal


def evidence(net_delta="154.00"):
    case = Obj(
        id=10,
        company_id=1,
        status="review_required",
        original_payroll_calculation_id=30,
        original_severance_source_id=100,
    )
    proposal = Obj(
        id=20,
        company_id=1,
        correction_case_id=10,
        status="review_required",
    )
    lineage = Obj(
        company_id=1,
        financial_proposal_id=20,
        original_source_id=100,
        corrected_source_id=101,
        original_revision=1,
        corrected_revision=2,
        original_gross=D("1000.00"),
        corrected_gross=D("1200.00"),
        source_chain_verified=True,
        proposed_gross_reconciled=True,
        posting_authorized=False,
    )
    deltas = Obj(
        company_id=1,
        case_id=10,
        proposal_id=20,
        original_source_id=100,
        original_calculation_id=30,
        original_gross=D("1000.00"),
        corrected_gross=D("1200.00"),
        gross_delta=D("200.00"),
        personal_income_tax_delta=D("36.00"),
        military_levy_delta=D("10.00"),
        employer_contribution_delta=D("0.00"),
        net_delta=D(net_delta),
        reconciled=True,
    )
    readiness = Obj(
        company_id=1,
        financial_proposal_id=20,
        corrected_source_chain_verified=True,
        proposed_gross_reconciled=True,
        posting_authorized=False,
        blockers=(
            "corrected_trusted_statutory_source_required",
        ),
    )
    return case, proposal, lineage, deltas, readiness


def build(data=None):
    case, proposal, lineage, deltas, readiness = (
        data if data is not None else evidence()
    )
    return build_severance_postpaid_correction_package(
        company_id=1,
        case=case,
        proposal=proposal,
        lineage=lineage,
        deltas=deltas,
        readiness=readiness,
    )


def test_positive_delta_package():
    result = build()

    assert result.gross_delta == D("200.00")
    assert result.net_delta == D("154.00")
    assert result.settlement_direction == "additional_payment"
    assert result.settlement_amount == D("154.00")
    assert result.corrected_severance_source_id == 101
    assert result.source_lineage_verified
    assert result.financial_delta_reconciled

    assert not result.statutory_tax_verified
    assert not result.posting_authorized
    assert not result.payment_authorized


def test_negative_delta_produces_recovery_direction():
    rows = list(evidence())

    rows[2].corrected_gross = D("800.00")
    rows[3].corrected_gross = D("800.00")
    rows[3].gross_delta = D("-200.00")
    rows[3].personal_income_tax_delta = D("-36.00")
    rows[3].military_levy_delta = D("-10.00")
    rows[3].net_delta = D("-154.00")

    result = build(rows)

    assert result.settlement_direction == "overpayment_recovery"
    assert result.settlement_amount == D("154.00")
    assert not result.payment_authorized


def test_zero_net_delta_does_not_authorize_settlement():
    rows = list(evidence())

    rows[3].personal_income_tax_delta = D("190.00")
    rows[3].military_levy_delta = D("10.00")
    rows[3].net_delta = D("0.00")

    result = build(rows)

    assert result.settlement_direction == "no_net_settlement"
    assert result.settlement_amount == D("0.00")
    assert not result.posting_authorized


@pytest.mark.parametrize(
    "field,value",
    [
        ("company_id", 2),
        ("source_chain_verified", False),
        ("proposed_gross_reconciled", False),
        ("posting_authorized", True),
        ("original_source_id", 999),
        ("corrected_source_id", 100),
        ("corrected_revision", 1),
        ("corrected_gross", D("1199.00")),
    ],
)
def test_invalid_source_lineage_rejected(field, value):
    rows = list(evidence())
    setattr(rows[2], field, value)

    with pytest.raises(SeverancePostpaidPackageError):
        build(rows)


@pytest.mark.parametrize(
    "field,value",
    [
        ("company_id", 2),
        ("case_id", 11),
        ("proposal_id", 21),
        ("original_source_id", 999),
        ("original_calculation_id", 999),
        ("gross_delta", D("201.00")),
        ("net_delta", D("155.00")),
        ("reconciled", False),
        ("military_levy_delta", D("NaN")),
    ],
)
def test_financial_delta_mismatch_rejected(field, value):
    rows = list(evidence())
    setattr(rows[3], field, value)

    with pytest.raises(SeverancePostpaidPackageError):
        build(rows)


@pytest.mark.parametrize(
    "field,value",
    [
        ("company_id", 2),
        ("corrected_source_chain_verified", False),
        ("proposed_gross_reconciled", False),
        ("posting_authorized", True),
    ],
)
def test_execution_readiness_mismatch_rejected(field, value):
    rows = list(evidence())
    setattr(rows[4], field, value)

    with pytest.raises(SeverancePostpaidPackageError):
        build(rows)


def test_missing_proposal_rejected():
    rows = list(evidence())
    rows[1] = None

    with pytest.raises(SeverancePostpaidPackageError):
        build(rows)


@pytest.mark.asyncio
async def test_missing_db_proposal_fails_closed():
    db = Obj(scalar=AsyncMock(return_value=None))

    with pytest.raises(
        SeverancePostpaidPackageError,
        match="financial proposal not found",
    ):
        await load_severance_postpaid_correction_package(
            db,
            company_id=1,
            financial_proposal_id=20,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "company_id,proposal_id",
    [
        (0, 20),
        (1, 0),
        (True, 20),
        (1, True),
        (-1, 20),
    ],
)
async def test_invalid_db_identity_rejected_before_query(
    company_id,
    proposal_id,
):
    db = Obj(scalar=AsyncMock())

    with pytest.raises(SeverancePostpaidPackageError):
        await load_severance_postpaid_correction_package(
            db,
            company_id=company_id,
            financial_proposal_id=proposal_id,
        )

    db.scalar.assert_not_awaited()


def test_no_approval_can_be_derived_from_financial_proposal():
    rows = list(evidence())
    rows[4].blockers = ()

    result = build(rows)

    assert (
        "corrected_trusted_statutory_source_required"
        in result.blockers
    )
    assert (
        "postpaid_financial_settlement_approval_required"
        in result.blockers
    )

    assert not result.statutory_tax_verified
    assert not result.posting_authorized
    assert not result.payment_authorized
