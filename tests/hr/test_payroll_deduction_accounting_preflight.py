"""D3-B2 accounting preflight safety tests."""

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.payroll_deduction_accounting_preflight import (
    AccountingPreflightError,
    evaluate_accounting_preflight,
)
from app.services.payroll_deduction_batch_limit_preview import (
    BatchLimitLine,
    BatchLimitPreview,
)

D = Decimal


def preview():
    return BatchLimitPreview(
        lines=(
            BatchLimitLine(
                instruction_id=11,
                priority=1,
                requested_amount=D("100.00"),
                permitted_amount=D("40.00"),
                unapplied_amount=D("60.00"),
            ),
            BatchLimitLine(
                instruction_id=12,
                priority=2,
                requested_amount=D("50.00"),
                permitted_amount=D("10.00"),
                unapplied_amount=D("40.00"),
            ),
        ),
        total_requested=D("150.00"),
        total_permitted=D("50.00"),
        total_unapplied=D("100.00"),
        remaining_global_capacity=D("0.00"),
    )


def run(**changes):
    args = dict(
        company_id=7,
        instruction_id=11,
        requested_amount=D("100.00"),
        d1_preflight=SimpleNamespace(
            financial_execution_authorized=False,
        ),
        d2_preview=preview(),
    )
    args.update(changes)
    return evaluate_accounting_preflight(**args)


def test_valid_preview_is_not_financial_authorization():
    result = run()

    assert result.candidate_permitted_amount == D("40.00")
    assert result.candidate_unapplied_amount == D("60.00")
    assert result.financial_execution_authorized is False
    assert result.journal_posting_allowed is False
    assert result.legal_authorization == "NOT_AUTHORIZED"


def test_second_instruction():
    result = run(
        instruction_id=12,
        requested_amount=D("50.00"),
    )

    assert result.candidate_permitted_amount == D("10.00")


@pytest.mark.parametrize(
    "changes",
    [
        {"company_id": 0},
        {"company_id": True},
        {"instruction_id": -1},
        {"instruction_id": 999},
        {"requested_amount": D("-1.00")},
        {"requested_amount": D("1.001")},
        {"requested_amount": D("NaN")},
        {"requested_amount": D("99.00")},
        {"d1_preflight": None},
        {
            "d1_preflight": SimpleNamespace(
                financial_execution_authorized=True,
            )
        },
    ],
)
def test_invalid_inputs_fail_closed(changes):
    with pytest.raises(AccountingPreflightError):
        run(**changes)


def test_d2_authorization_bypass_rejected():
    with pytest.raises(AccountingPreflightError):
        run(
            d2_preview=replace(
                preview(),
                financial_execution_authorized=True,
            )
        )


def test_d2_legal_authorization_bypass_rejected():
    with pytest.raises(AccountingPreflightError):
        run(
            d2_preview=replace(
                preview(),
                legal_authorization="AUTHORIZED",
            )
        )


def test_incorrect_batch_total_rejected():
    with pytest.raises(AccountingPreflightError):
        run(
            d2_preview=replace(
                preview(),
                total_permitted=D("51.00"),
            )
        )


def test_duplicate_instruction_rejected():
    original = preview()

    with pytest.raises(AccountingPreflightError):
        run(
            d2_preview=replace(
                original,
                lines=(original.lines[0], original.lines[0]),
            )
        )


def test_negative_allocated_amount_rejected():
    original = preview()
    invalid = replace(
        original.lines[0],
        permitted_amount=D("-1.00"),
    )

    with pytest.raises(AccountingPreflightError):
        run(
            d2_preview=replace(
                original,
                lines=(invalid, original.lines[1]),
            )
        )


def test_line_conservation_rejected():
    original = preview()
    invalid = replace(
        original.lines[0],
        unapplied_amount=D("59.00"),
    )

    with pytest.raises(AccountingPreflightError):
        run(
            d2_preview=replace(
                original,
                lines=(invalid, original.lines[1]),
            )
        )
