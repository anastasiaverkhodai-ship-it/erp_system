"""D4-B1 remittance readiness: strict non-payment boundary."""

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.payroll_deduction_remittance_readiness import (
    RemittanceReadinessError,
    evaluate_remittance_readiness,
)


D = Decimal


def accounting_line(**changes):
    fields = dict(
        company_id=7,
        instruction_id=11,
        requested_amount=D("100.00"),
        candidate_permitted_amount=D("40.00"),
        candidate_unapplied_amount=D("60.00"),
        financial_execution_authorized=False,
        journal_posting_allowed=False,
        legal_authorization="NOT_AUTHORIZED",
    )
    fields.update(changes)
    return SimpleNamespace(**fields)


def accounting(**changes):
    fields = dict(
        company_id=7,
        accounting_preflights=(accounting_line(),),
        financial_execution_authorized=False,
        journal_posting_allowed=False,
        legal_authorization="NOT_AUTHORIZED",
    )
    fields.update(changes)
    return SimpleNamespace(**fields)


def remittance(**changes):
    fields = dict(
        company_id=7,
        instruction_id=11,
        technical_reconciled=True,
        withheld_funds_verified=False,
        bank_payment_verified=False,
        payment_execution_allowed=False,
        payroll_posting_allowed=False,
        legal_authorization="NOT_AUTHORIZED",
    )
    fields.update(changes)
    return SimpleNamespace(**fields)


def check(**changes):
    args = dict(
        company_id=7,
        instruction_id=11,
        accounting_orchestration=accounting(),
        remittance_reconciliation=remittance(),
    )
    args.update(changes)
    return evaluate_remittance_readiness(**args)


def test_valid_technical_readiness_does_not_authorize_payment():
    result = check()

    assert result.candidate_permitted_amount == D("40.00")
    assert result.candidate_unapplied_amount == D("60.00")
    assert result.technical_reconciled is True
    assert result.withheld_funds_verified is False
    assert result.bank_payment_verified is False
    assert result.financial_execution_authorized is False
    assert result.journal_posting_allowed is False
    assert result.payment_execution_allowed is False
    assert result.legal_authorization == "NOT_AUTHORIZED"


@pytest.mark.parametrize(
    "changes",
    [
        {"company_id": 0},
        {"company_id": True},
        {"instruction_id": 0},
        {"accounting_orchestration": None},
        {"remittance_reconciliation": None},
        {"accounting_orchestration": accounting(company_id=8)},
        {"remittance_reconciliation": remittance(company_id=8)},
        {"remittance_reconciliation": remittance(instruction_id=12)},
        {"remittance_reconciliation": remittance(technical_reconciled=False)},
        {"remittance_reconciliation": remittance(withheld_funds_verified=True)},
        {"remittance_reconciliation": remittance(bank_payment_verified=True)},
        {"remittance_reconciliation": remittance(payment_execution_allowed=True)},
        {"remittance_reconciliation": remittance(payroll_posting_allowed=True)},
        {"remittance_reconciliation": remittance(legal_authorization="AUTHORIZED")},
        {"accounting_orchestration": accounting(financial_execution_authorized=True)},
        {"accounting_orchestration": accounting(journal_posting_allowed=True)},
        {"accounting_orchestration": accounting(legal_authorization="AUTHORIZED")},
    ],
)
def test_untrusted_or_inconsistent_inputs_fail_closed(changes):
    with pytest.raises(RemittanceReadinessError):
        check(**changes)


@pytest.mark.parametrize(
    "row_changes",
    [
        {"company_id": 8},
        {"requested_amount": D("99.00")},
        {"requested_amount": D("NaN")},
        {"requested_amount": D("-1.00")},
        {"requested_amount": D("0.001")},
        {"candidate_permitted_amount": D("101.00")},
        {"candidate_unapplied_amount": D("-1.00")},
        {"financial_execution_authorized": True},
        {"journal_posting_allowed": True},
        {"legal_authorization": "AUTHORIZED"},
    ],
)
def test_invalid_accounting_lines_fail_closed(row_changes):
    with pytest.raises(RemittanceReadinessError):
        check(
            accounting_orchestration=accounting(
                accounting_preflights=(
                    accounting_line(**row_changes),
                )
            )
        )


def test_missing_instruction_rejected():
    with pytest.raises(RemittanceReadinessError):
        check(
            accounting_orchestration=accounting(
                accounting_preflights=(
                    accounting_line(instruction_id=12),
                )
            )
        )


def test_duplicate_accounting_instruction_rejected():
    row = accounting_line()

    with pytest.raises(RemittanceReadinessError):
        check(
            accounting_orchestration=accounting(
                accounting_preflights=(row, row),
            )
        )


def test_empty_accounting_preflights_rejected():
    with pytest.raises(RemittanceReadinessError):
        check(
            accounting_orchestration=accounting(
                accounting_preflights=(),
            )
        )


def test_caller_cannot_turn_candidate_amount_into_payment():
    result = check()

    # A technical candidate remains only a candidate.
    assert result.candidate_permitted_amount > D("0.00")
    assert result.payment_execution_allowed is False
    assert result.withheld_funds_verified is False
    assert result.bank_payment_verified is False


def test_result_is_immutable():
    result = check()

    with pytest.raises(Exception):
        result.payment_execution_allowed = True
