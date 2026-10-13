"""Phase 13 C5-B: negative financial-safety regression.

The tests use pure reconciliation evidence.
No payment execution, payroll posting or database writes.
"""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.services.payroll_deduction_remittance_reconciliation import (
    InstructionEvidence,
    RemainderEvidence,
    RemittanceEvidence,
    RemittanceReconciliationError,
    reconcile_remittance_sources,
)

from app.services.payroll_deduction_remittance_persistence_service import (
    RemittancePersistenceError,
    _id,
    _transaction,
)


def snapshots():
    instruction = InstructionEvidence(
        company_id=15,
        instruction_id=25,
        original_amount=Decimal("100.00"),
        currency_code="UAH",
    )

    remainder = RemainderEvidence(
        company_id=15,
        instruction_id=25,
        original_amount=Decimal("100.00"),
        currency_code="UAH",
        applied_amount=Decimal("30.00"),
        remaining_amount=Decimal("70.00"),
        event_count=2,
    )

    remittance = RemittanceEvidence(
        company_id=15,
        instruction_id=25,
        beneficiary_reference="BENEFICIARY-C5",
        currency_code="UAH",
        available_amount=Decimal("80.00"),
        allocated_amount=Decimal("20.00"),
        remaining_amount=Decimal("60.00"),
        event_count=1,
    )

    return instruction, remainder, remittance


def reconcile(items):
    return reconcile_remittance_sources(
        instruction=items[0],
        remainder=items[1],
        remittance=items[2],
    )


@pytest.mark.parametrize(
    "invalid",
    [None, False, True, 0, -1, "1", 1.0],
)
def test_persistence_invalid_company_id(invalid):
    with pytest.raises(RemittancePersistenceError):
        _id(invalid, "company_id")


@pytest.mark.parametrize(
    "invalid",
    [None, False, True, 0, -1, "5", 5.0],
)
def test_persistence_invalid_instruction_id(invalid):
    with pytest.raises(RemittancePersistenceError):
        _id(invalid, "instruction_id")


def test_session_must_be_async_session():
    with pytest.raises(
        RemittancePersistenceError,
        match="AsyncSession required",
    ):
        _transaction(None)


def test_baseline_conservation_and_no_authorization():
    result = reconcile(snapshots())

    assert result.technical_reconciled is True
    assert result.withheld_funds_verified is False
    assert result.bank_payment_verified is False
    assert result.payment_execution_allowed is False
    assert result.payroll_posting_allowed is False
    assert result.legal_authorization == "NOT_AUTHORIZED"


@pytest.mark.parametrize(
    "bad",
    [
        Decimal("-1.00"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1.001"),
        "10.00",
        None,
    ],
)
def test_invalid_remittance_amount(bad):
    instruction, remainder, remittance = snapshots()
    bad_remittance = replace(
        remittance,
        allocated_amount=bad,
    )

    with pytest.raises(RemittanceReconciliationError):
        reconcile(
            (instruction, remainder, bad_remittance)
        )


@pytest.mark.parametrize(
    "bad",
    [
        Decimal("-1.00"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("0.001"),
        "30.00",
        None,
    ],
)
def test_invalid_remainder_amount(bad):
    instruction, remainder, remittance = snapshots()
    bad_remainder = replace(
        remainder,
        applied_amount=bad,
    )

    with pytest.raises(RemittanceReconciliationError):
        reconcile(
            (instruction, bad_remainder, remittance)
        )


@pytest.mark.parametrize(
    "currency",
    ["usd", "EURO", "U1H", "", " UAH", None],
)
def test_bad_currency_rejected(currency):
    instruction, remainder, remittance = snapshots()

    with pytest.raises(RemittanceReconciliationError):
        reconcile(
            (
                instruction,
                remainder,
                replace(
                    remittance,
                    currency_code=currency,
                ),
            )
        )


def test_cross_company_remainder_rejected():
    instruction, remainder, remittance = snapshots()

    with pytest.raises(
        RemittanceReconciliationError,
        match="Company isolation mismatch",
    ):
        reconcile(
            (
                instruction,
                replace(remainder, company_id=16),
                remittance,
            )
        )


def test_cross_company_remittance_rejected():
    instruction, remainder, remittance = snapshots()

    with pytest.raises(
        RemittanceReconciliationError,
        match="Company isolation mismatch",
    ):
        reconcile(
            (
                instruction,
                remainder,
                replace(remittance, company_id=16),
            )
        )


def test_source_identity_mismatch_rejected():
    instruction, remainder, remittance = snapshots()

    with pytest.raises(
        RemittanceReconciliationError,
        match="Instruction identity mismatch",
    ):
        reconcile(
            (
                instruction,
                remainder,
                replace(
                    remittance,
                    instruction_id=26,
                ),
            )
        )


def test_remainder_conservation_tampering():
    instruction, remainder, remittance = snapshots()

    with pytest.raises(
        RemittanceReconciliationError,
        match="Remainder conservation mismatch",
    ):
        reconcile(
            (
                instruction,
                replace(
                    remainder,
                    remaining_amount=Decimal("69.99"),
                ),
                remittance,
            )
        )


def test_remittance_conservation_tampering():
    instruction, remainder, remittance = snapshots()

    with pytest.raises(
        RemittanceReconciliationError,
        match="Remittance conservation mismatch",
    ):
        reconcile(
            (
                instruction,
                remainder,
                replace(
                    remittance,
                    remaining_amount=Decimal("59.99"),
                ),
            )
        )


def test_plan_ceiling_exceeds_source():
    instruction, remainder, remittance = snapshots()

    with pytest.raises(
        RemittanceReconciliationError,
        match="Remittance ceiling exceeds instruction",
    ):
        reconcile(
            (
                instruction,
                remainder,
                replace(
                    remittance,
                    available_amount=Decimal("110.00"),
                    remaining_amount=Decimal("90.00"),
                ),
            )
        )


def test_remainder_event_count_cannot_be_negative():
    instruction, remainder, remittance = snapshots()

    with pytest.raises(RemittanceReconciliationError):
        reconcile(
            (
                instruction,
                replace(remainder, event_count=-1),
                remittance,
            )
        )


def test_remittance_event_count_cannot_be_negative():
    instruction, remainder, remittance = snapshots()

    with pytest.raises(RemittanceReconciliationError):
        reconcile(
            (
                instruction,
                remainder,
                replace(remittance, event_count=-1),
            )
        )


def test_no_remittance_allocation_without_event():
    instruction, remainder, remittance = snapshots()

    with pytest.raises(
        RemittanceReconciliationError,
        match="Remittance allocated without events",
    ):
        reconcile(
            (
                instruction,
                remainder,
                replace(remittance, event_count=0),
            )
        )


def test_no_remainder_allocation_without_event():
    instruction, remainder, remittance = snapshots()

    with pytest.raises(
        RemittanceReconciliationError,
        match="Remainder applied without events",
    ):
        reconcile(
            (
                instruction,
                replace(remainder, event_count=0),
                remittance,
            )
        )


def test_technical_reconciliation_is_not_payment_proof():
    instruction, remainder, remittance = snapshots()

    # Technical ledgers can match while real payment proof
    # remains entirely absent.
    result = reconcile(
        (
            instruction,
            replace(
                remainder,
                applied_amount=Decimal("20.00"),
                remaining_amount=Decimal("80.00"),
                event_count=1,
            ),
            remittance,
        )
    )

    assert result.remainder_applied_amount == (
        result.remittance_allocated_amount
    )

    assert result.technical_reconciled is True
    assert result.withheld_funds_verified is False
    assert result.bank_payment_verified is False
    assert result.payment_execution_allowed is False
    assert result.payroll_posting_allowed is False
    assert result.legal_authorization == "NOT_AUTHORIZED"
