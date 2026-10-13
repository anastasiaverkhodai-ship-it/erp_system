"""Phase 13 C4-A remittance rule contracts."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from app.services.payroll_deduction_remittance_rules import (
    RemittanceRuleError,
    RemittanceSource,
    RemittanceEvent,
    RemittancePlan,
    open_remittance_plan,
    append_remittance_event,
    reconcile_remittance_plan,
)


def opening(**changes):
    values = {
        "company_id": 1,
        "instruction_id": 20,
        "beneficiary_reference": "BENEFICIARY-TEST",
        "currency_code": "UAH",
        "available_amount": Decimal("100.00"),
    }

    values.update(changes)
    return open_remittance_plan(**values)


def event(event_id="e1", amount="30.00", **changes):
    values = {
        "event_id": event_id,
        "company_id": 1,
        "instruction_id": 20,
        "beneficiary_reference": "BENEFICIARY-TEST",
        "currency_code": "UAH",
        "amount": Decimal(amount),
    }

    values.update(changes)
    return RemittanceEvent(**values)


def append(plan, candidate):
    return append_remittance_event(
        plan=plan,
        event=candidate,
    )


def test_empty_plan():
    balance = reconcile_remittance_plan(opening())

    assert balance.available_amount == Decimal("100.00")
    assert balance.allocated_amount == Decimal("0.00")
    assert balance.remaining_amount == Decimal("100.00")
    assert balance.event_count == 0


def test_partial_remittance_allocations():
    first = append(opening(), event())

    assert first.applied_now == Decimal("30.00")
    assert first.balance.remaining_amount == Decimal("70.00")

    second = append(
        first.plan,
        event("e2", "20.00"),
    )

    assert second.balance.allocated_amount == Decimal("50.00")
    assert second.balance.remaining_amount == Decimal("50.00")
    assert second.balance.event_count == 2


def test_full_allocation():
    result = append(
        opening(),
        event(amount="100.00"),
    )

    assert result.balance.remaining_amount == Decimal("0.00")


def test_overallocation_rejected():
    first = append(opening(), event())

    with pytest.raises(
        RemittanceRuleError,
        match="exceeds available amount",
    ):
        append(first.plan, event("e2", "71.00"))


def test_exact_replay_is_noop():
    first = append(opening(), event())
    repeated = append(first.plan, event())

    assert repeated.duplicate is True
    assert repeated.applied_now == Decimal("0.00")
    assert repeated.plan == first.plan
    assert repeated.balance.event_count == 1


def test_changed_payload_replay_rejected():
    first = append(opening(), event())

    with pytest.raises(
        RemittanceRuleError,
        match="different payload",
    ):
        append(first.plan, event(amount="31.00"))


def test_company_isolation():
    with pytest.raises(
        RemittanceRuleError,
        match="Cross-company",
    ):
        append(
            opening(),
            event(company_id=2),
        )


def test_instruction_isolation():
    with pytest.raises(
        RemittanceRuleError,
        match="Instruction mismatch",
    ):
        append(
            opening(),
            event(instruction_id=21),
        )


def test_beneficiary_binding():
    with pytest.raises(
        RemittanceRuleError,
        match="Beneficiary mismatch",
    ):
        append(
            opening(),
            event(
                beneficiary_reference="OTHER-BENEFICIARY"
            ),
        )


def test_currency_binding():
    with pytest.raises(
        RemittanceRuleError,
        match="Currency mismatch",
    ):
        append(
            opening(),
            event(currency_code="EUR"),
        )


@pytest.mark.parametrize(
    "value",
    ["-1.00", "1.001", "NaN", "Infinity"],
)
def test_invalid_available_amount(value):
    with pytest.raises(RemittanceRuleError):
        opening(available_amount=Decimal(value))


@pytest.mark.parametrize(
    "value",
    ["0.00", "-1.00", "1.001", "NaN", "Infinity"],
)
def test_invalid_event_amount(value):
    with pytest.raises(RemittanceRuleError):
        event(amount=value)


@pytest.mark.parametrize(
    "code",
    ["uah", "UA", "UAHH", "U1H", ""],
)
def test_invalid_currency(code):
    with pytest.raises(RemittanceRuleError):
        opening(currency_code=code)


@pytest.mark.parametrize(
    "beneficiary",
    ["", " ", " bad", "bad ", "bad\nreference"],
)
def test_invalid_beneficiary(beneficiary):
    with pytest.raises(RemittanceRuleError):
        opening(beneficiary_reference=beneficiary)


@pytest.mark.parametrize(
    "identifier",
    [0, -1, True, "1"],
)
def test_invalid_company_id(identifier):
    with pytest.raises(RemittanceRuleError):
        opening(company_id=identifier)


def test_duplicate_history_is_rejected():
    candidate = event()

    with pytest.raises(
        RemittanceRuleError,
        match="Duplicate remittance event",
    ):
        RemittancePlan(
            source=opening().source,
            events=(candidate, candidate),
        )


def test_reconstruction_rejects_overallocation():
    with pytest.raises(
        RemittanceRuleError,
        match="exceeds available amount",
    ):
        RemittancePlan(
            source=opening().source,
            events=(
                event("e1", "60.00"),
                event("e2", "50.00"),
            ),
        )


def test_source_is_immutable():
    source = opening().source

    with pytest.raises(FrozenInstanceError):
        source.available_amount = Decimal("200.00")


def test_event_is_immutable():
    candidate = event()

    with pytest.raises(FrozenInstanceError):
        candidate.amount = Decimal("200.00")


def test_no_execution_authorization():
    result = append(opening(), event())

    assert result.payment_execution_allowed is False
    assert result.legal_authorization == "NOT_AUTHORIZED"

    assert result.balance.payment_execution_allowed is False
    assert result.balance.payroll_posting_allowed is False
    assert result.balance.legal_authorization == "NOT_AUTHORIZED"


def test_no_database_or_payment_execution_dependencies():
    from pathlib import Path

    source = Path(
        "app/services/payroll_deduction_remittance_rules.py"
    ).read_text(encoding="utf-8")

    assert "AsyncSession" not in source
    assert "db.commit(" not in source
    assert "db.rollback(" not in source
    assert "create_payment(" not in source
    assert "post_journal_entry(" not in source
