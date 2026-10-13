"""Phase 13 C4-D2 independent reconciliation tests."""

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import pytest

from app.services.payroll_deduction_remittance_reconciliation import (
    InstructionEvidence,
    RemainderEvidence,
    RemittanceEvidence,
    RemittanceReconciliationError,
    reconcile_remittance_sources,
)


def evidence():
    return (
        InstructionEvidence(
            company_id=1,
            instruction_id=10,
            original_amount=Decimal("100.00"),
            currency_code="UAH",
        ),
        RemainderEvidence(
            company_id=1,
            instruction_id=10,
            original_amount=Decimal("100.00"),
            currency_code="UAH",
            applied_amount=Decimal("40.00"),
            remaining_amount=Decimal("60.00"),
            event_count=2,
        ),
        RemittanceEvidence(
            company_id=1,
            instruction_id=10,
            beneficiary_reference="TEST-BENEFICIARY",
            currency_code="UAH",
            available_amount=Decimal("80.00"),
            allocated_amount=Decimal("30.00"),
            remaining_amount=Decimal("50.00"),
            event_count=1,
        ),
    )


def check(items):
    return reconcile_remittance_sources(
        instruction=items[0],
        remainder=items[1],
        remittance=items[2],
    )


def changed(index, **values):
    items = list(evidence())
    items[index] = replace(items[index], **values)
    return tuple(items)


def blocked(index, match, **values):
    with pytest.raises(
        RemittanceReconciliationError,
        match=match,
    ):
        check(changed(index, **values))


def test_valid_independent_reconciliation():
    result = check(evidence())

    assert result.technical_reconciled is True
    assert result.instruction_amount == Decimal("100.00")
    assert result.remainder_applied_amount == Decimal("40.00")
    assert result.remainder_remaining_amount == Decimal("60.00")
    assert result.remittance_ceiling == Decimal("80.00")
    assert result.remittance_allocated_amount == Decimal("30.00")
    assert result.remittance_remaining_amount == Decimal("50.00")


def test_remainder_and_remittance_are_not_equated():
    result = check(evidence())

    assert (
        result.remainder_applied_amount
        != result.remittance_allocated_amount
    )

    assert result.technical_reconciled is True
    assert result.withheld_funds_verified is False


def test_no_payment_authorization():
    result = check(evidence())

    assert result.bank_payment_verified is False
    assert result.withheld_funds_verified is False
    assert result.payment_execution_allowed is False
    assert result.payroll_posting_allowed is False
    assert result.legal_authorization == "NOT_AUTHORIZED"


def test_company_isolation_remainder():
    blocked(1, "Company isolation mismatch", company_id=2)


def test_company_isolation_remittance():
    blocked(2, "Company isolation mismatch", company_id=2)


def test_instruction_isolation_remainder():
    blocked(1, "Instruction identity mismatch", instruction_id=11)


def test_instruction_isolation_remittance():
    blocked(2, "Instruction identity mismatch", instruction_id=11)


def test_currency_mismatch_remainder():
    blocked(1, "Currency mismatch", currency_code="EUR")


def test_currency_mismatch_remittance():
    blocked(2, "Currency mismatch", currency_code="EUR")


def test_source_opening_tampering():
    blocked(
        1,
        "Remainder opening differs",
        original_amount=Decimal("99.00"),
    )


def test_remainder_conservation():
    blocked(
        1,
        "Remainder conservation mismatch",
        remaining_amount=Decimal("59.00"),
    )


def test_remittance_conservation():
    blocked(
        2,
        "Remittance conservation mismatch",
        remaining_amount=Decimal("49.00"),
    )


def test_remittance_ceiling_not_above_instruction():
    blocked(
        2,
        "Remittance ceiling exceeds instruction amount",
        available_amount=Decimal("110.00"),
        remaining_amount=Decimal("80.00"),
    )


def test_beneficiary_must_not_be_blank():
    blocked(
        2,
        "Invalid beneficiary",
        beneficiary_reference=" ",
    )


def test_beneficiary_cannot_contain_control_characters():
    blocked(
        2,
        "Invalid beneficiary",
        beneficiary_reference="TEST\nOTHER",
    )


def test_zero_events_cannot_have_applied_remainder():
    blocked(
        1,
        "Remainder applied without events",
        event_count=0,
    )


def test_zero_events_cannot_have_allocated_remittance():
    blocked(
        2,
        "Remittance allocated without events",
        event_count=0,
    )


def test_positive_event_count_requires_positive_remainder_total():
    blocked(
        1,
        "Remainder events have zero aggregate",
        event_count=2,
        applied_amount=Decimal("0.00"),
        remaining_amount=Decimal("100.00"),
    )


def test_positive_event_count_requires_positive_remittance_total():
    blocked(
        2,
        "Remittance events have zero aggregate",
        event_count=1,
        allocated_amount=Decimal("0.00"),
        remaining_amount=Decimal("80.00"),
    )


@pytest.mark.parametrize(
    "bad_amount",
    [
        Decimal("-1.00"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1.001"),
    ],
)
def test_invalid_monetary_evidence(bad_amount):
    blocked(
        2,
        "remittance allocated",
        allocated_amount=bad_amount,
    )


@pytest.mark.parametrize(
    "invalid_id",
    [0, -1, True, "1"],
)
def test_invalid_identity(invalid_id):
    blocked(
        0,
        "company_id",
        company_id=invalid_id,
    )


@pytest.mark.parametrize(
    "invalid_count",
    [-1, True, "1"],
)
def test_invalid_event_count(invalid_count):
    blocked(
        2,
        "event_count",
        event_count=invalid_count,
    )


def test_immutable_reconciliation_result():
    result = check(evidence())

    with pytest.raises(FrozenInstanceError):
        result.payment_execution_allowed = True


def test_service_has_no_database_dependency():
    import inspect
    import app.services.payroll_deduction_remittance_reconciliation as module

    source = inspect.getsource(module)

    assert "AsyncSession" not in source
    assert "db.commit(" not in source
    assert "db.rollback(" not in source
    assert "execute_bank_transfer(" not in source
    assert "post_journal_entry(" not in source


def test_invalid_evidence_types_rejected():
    instruction, remainder, remittance = evidence()

    with pytest.raises(
        RemittanceReconciliationError,
        match="Invalid instruction evidence",
    ):
        reconcile_remittance_sources(
            instruction=None,
            remainder=remainder,
            remittance=remittance,
        )

    with pytest.raises(
        RemittanceReconciliationError,
        match="Invalid remainder evidence",
    ):
        reconcile_remittance_sources(
            instruction=instruction,
            remainder=None,
            remittance=remittance,
        )

    with pytest.raises(
        RemittanceReconciliationError,
        match="Invalid remittance evidence",
    ):
        reconcile_remittance_sources(
            instruction=instruction,
            remainder=remainder,
            remittance=None,
        )
