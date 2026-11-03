from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from app.services.payroll_severance_financial_approval_gate import (
    FinancialReviewEvidence,
    SeveranceApprovalGateError,
    financial_approval_preflight,
    snapshot_from_json,
)
from app.services.payroll_severance_net_tax_settlement import (
    SeveranceFinancialSnapshot,
)


def snap(gross):
    return SeveranceFinancialSnapshot(
        gross=D(gross),
        personal_income_tax=D("1800.00"),
        military_levy=D("500.00"),
        net=D(gross) - D("2300.00"),
        employer_contribution=D("0.00"),
    )


def data():
    original = snap("10000.00")
    corrected = snap("10100.00")

    def as_json(item):
        return {
            key: str(getattr(item, key))
            for key in (
                "gross", "personal_income_tax",
                "military_levy", "net",
                "employer_contribution",
            )
        }

    case = SimpleNamespace(
        id=10,
        company_id=1,
        status="review_required",
        original_severance_gross=D("10000.00"),
    )
    proposal = SimpleNamespace(
        company_id=1,
        correction_case_id=10,
        submitted_by=20,
        status="review_required",
        original_financial=as_json(original),
        proposed_financial=as_json(corrected),
    )
    evidence = FinancialReviewEvidence(
        reviewer_id=21,
        submitter_id=20,
        document_reference="INDEPENDENT-REVIEW-001",
        normative_source_reference="STATUTORY-SOURCE-REVIEW-001",
        reviewed_original=original,
        reviewed_corrected=corrected,
    )
    return proposal, case, evidence


def run(items):
    proposal, case, evidence = items
    return financial_approval_preflight(
        proposal=proposal,
        case=case,
        evidence=evidence,
    )


def test_reconciled_review_does_not_authorize_posting():
    result = run(data())
    assert result.gross_delta == D("100.00")
    assert result.net_delta == D("100.00")
    assert result.reviewer_separated
    assert result.financial_integrity_reconciled
    assert not result.statutory_source_verified
    assert not result.journal_posting_authorized
    assert not result.tax_refund_authorized


def test_same_person_cannot_review():
    proposal, case, evidence = data()
    with pytest.raises(
        SeveranceApprovalGateError,
        match="Independent reviewer",
    ):
        run((
            proposal,
            case,
            replace(evidence, reviewer_id=20),
        ))


def test_changed_corrected_amount_is_rejected():
    proposal, case, evidence = data()
    changed = snap("10100.01")
    with pytest.raises(
        SeveranceApprovalGateError,
        match="differ",
    ):
        run((
            proposal,
            case,
            replace(evidence, reviewed_corrected=changed),
        ))


def test_changed_original_source_is_rejected():
    proposal, case, evidence = data()
    case.original_severance_gross = D("10000.01")
    with pytest.raises(
        SeveranceApprovalGateError,
        match="pinned case",
    ):
        run((proposal, case, evidence))


@pytest.mark.parametrize(
    "field",
    [
        "statutory_source_verified",
        "journal_posting_authorized",
        "employee_recovery_authorized",
        "tax_refund_authorized",
    ],
)
def test_forged_authorization_is_rejected(field):
    proposal, case, evidence = data()
    with pytest.raises(
        SeveranceApprovalGateError,
        match="cannot self-authorize",
    ):
        run((
            proposal,
            case,
            replace(evidence, **{field: True}),
        ))


def test_invalid_stored_net_is_rejected():
    proposal, case, evidence = data()
    proposal.proposed_financial["net"] = "7800.01"

    with pytest.raises(SeveranceApprovalGateError):
        run((proposal, case, evidence))


def test_incomplete_stored_snapshot_is_rejected():
    proposal, case, evidence = data()
    del proposal.original_financial["military_levy"]

    with pytest.raises(SeveranceApprovalGateError):
        run((proposal, case, evidence))


@pytest.mark.parametrize(
    "bad",
    ["NaN", "Infinity", "-1.00", "10.001"],
)
def test_invalid_money_rejected(bad):
    proposal, case, evidence = data()
    proposal.proposed_financial["gross"] = bad

    with pytest.raises(SeveranceApprovalGateError):
        run((proposal, case, evidence))


def test_snapshot_requires_exact_fields():
    with pytest.raises(SeveranceApprovalGateError):
        snapshot_from_json({"gross": "10.00"})
