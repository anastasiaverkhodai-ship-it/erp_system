from datetime import date
from decimal import Decimal as D

import pytest

from app.services.payroll_severance_net_tax_settlement import (
    SeveranceFinancialSnapshot,
)
from app.services.payroll_severance_financial_proposal_service import (
    SeveranceFinancialProposalError,
    financial_json,
    validate_financial_proposal,
)


def snapshot(gross="10000.00"):
    gross = D(gross)
    return SeveranceFinancialSnapshot(
        gross=gross,
        personal_income_tax=D("1800.00"),
        military_levy=D("500.00"),
        net=gross - D("2300.00"),
        employer_contribution=D("0.00"),
    )


def request():
    return dict(
        original=snapshot(),
        proposed=snapshot("10100.00"),
        document_reference="SEVERANCE-CORRECTION-EVIDENCE-001",
        document_date=date(2026, 11, 3),
        request_key="severance-proposal-001",
        submitted_by=1,
    )


def test_original_and_corrected_financial_evidence():
    payload = validate_financial_proposal(**request())
    assert payload["original_financial"]["gross"] == "10000.00"
    assert payload["proposed_financial"]["gross"] == "10100.00"
    assert payload["proposed_financial"]["net"] == "7800.00"


def test_no_changed_amount_rejected():
    data = request()
    data["proposed"] = snapshot()
    with pytest.raises(
        SeveranceFinancialProposalError,
        match="no change",
    ):
        validate_financial_proposal(**data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("document_reference", "short"),
        ("document_date", "2026-11-03"),
        ("request_key", "x"),
        ("submitted_by", True),
        ("submitted_by", 0),
    ],
)
def test_invalid_evidence_rejected(field, value):
    data = request()
    data[field] = value
    with pytest.raises(SeveranceFinancialProposalError):
        validate_financial_proposal(**data)


def test_inconsistent_net_rejected():
    bad = SeveranceFinancialSnapshot(
        gross=D("10100.00"),
        personal_income_tax=D("1800.00"),
        military_levy=D("500.00"),
        net=D("7800.01"),
        employer_contribution=D("0.00"),
    )
    with pytest.raises(SeveranceFinancialProposalError):
        financial_json(bad)


def test_negative_tax_rejected():
    bad = SeveranceFinancialSnapshot(
        gross=D("10100.00"),
        personal_income_tax=D("-1.00"),
        military_levy=D("500.00"),
        net=D("9601.00"),
        employer_contribution=D("0.00"),
    )
    with pytest.raises(SeveranceFinancialProposalError):
        financial_json(bad)
