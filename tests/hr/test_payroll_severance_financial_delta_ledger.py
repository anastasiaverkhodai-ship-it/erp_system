from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from app.services.payroll_severance_financial_delta_ledger import (
    SeveranceDeltaError,
    calculate_severance_deltas,
)


def sources():
    case = SimpleNamespace(
        id=10,
        company_id=1,
        status="review_required",
        original_severance_source_id=100,
        original_payroll_calculation_id=20,
        original_severance_gross=D("10000.00"),
    )

    proposal = SimpleNamespace(
        id=30,
        company_id=1,
        correction_case_id=10,
        status="review_required",
        original_financial={
            "gross": "10000.00",
            "personal_income_tax": "1800.00",
            "military_levy": "500.00",
            "employer_contribution": "0.00",
            "net": "7700.00",
        },
        proposed_financial={
            "gross": "10100.00",
            "personal_income_tax": "1818.00",
            "military_levy": "505.00",
            "employer_contribution": "0.00",
            "net": "7777.00",
        },
    )
    return case, proposal


def test_components_and_net_reconcile():
    case, proposal = sources()

    result = calculate_severance_deltas(
        case=case, proposal=proposal
    )

    assert result.gross_delta == D("100.00")
    assert result.personal_income_tax_delta == D("18.00")
    assert result.military_levy_delta == D("5.00")
    assert result.net_delta == D("77.00")
    assert result.reconciled

    assert not result.statutory_source_verified
    assert not result.approval_persisted
    assert not result.journal_posting_authorized
    assert not result.employee_payment_authorized


def test_negative_correction_supported():
    case, proposal = sources()
    proposal.proposed_financial = {
        "gross": "9900.00",
        "personal_income_tax": "1782.00",
        "military_levy": "495.00",
        "employer_contribution": "0.00",
        "net": "7623.00",
    }

    result = calculate_severance_deltas(
        case=case, proposal=proposal
    )

    assert result.gross_delta == D("-100.00")
    assert result.net_delta == D("-77.00")
    assert not result.employee_recovery_authorized


@pytest.mark.parametrize(
    "field,value",
    [
        ("company_id", 2),
        ("correction_case_id", 11),
        ("status", "approved"),
    ],
)
def test_wrong_source_is_rejected(field, value):
    case, proposal = sources()
    setattr(proposal, field, value)

    with pytest.raises(SeveranceDeltaError):
        calculate_severance_deltas(
            case=case, proposal=proposal
        )


def test_original_source_gross_mismatch_rejected():
    case, proposal = sources()
    case.original_severance_gross = D("9999.99")

    with pytest.raises(
        SeveranceDeltaError,
        match="Original gross",
    ):
        calculate_severance_deltas(
            case=case, proposal=proposal
        )


def test_inconsistent_proposed_net_rejected():
    case, proposal = sources()
    proposal.proposed_financial["net"] = "7777.01"

    with pytest.raises(SeveranceDeltaError):
        calculate_severance_deltas(
            case=case, proposal=proposal
        )


def test_same_original_and_corrected_rejected():
    case, proposal = sources()
    proposal.proposed_financial = dict(
        proposal.original_financial
    )

    with pytest.raises(
        SeveranceDeltaError,
        match="No correction difference",
    ):
        calculate_severance_deltas(
            case=case, proposal=proposal
        )
