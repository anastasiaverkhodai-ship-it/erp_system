"""Phase 13 C4-C1 persistence safety and contract tests."""

import ast
import inspect
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import (
    payroll_deduction_remittance_persistence_service as service,
)
from app.services.payroll_deduction_remittance_rules import (
    RemittanceEvent,
    RemittanceRuleError,
    open_remittance_plan,
    append_remittance_event,
)


def test_public_persistence_operations_present():
    assert inspect.iscoroutinefunction(
        service.open_persisted_remittance_plan
    )
    assert inspect.iscoroutinefunction(
        service.append_persisted_remittance_event
    )
    assert inspect.iscoroutinefunction(
        service.read_persisted_remittance_balance
    )


def test_no_service_commit_or_rollback():
    source = inspect.getsource(service)
    tree = ast.parse(source)

    forbidden = {"commit", "rollback"}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        if isinstance(node.func, ast.Attribute):
            assert node.func.attr not in forbidden


def test_no_payment_execution_dependencies():
    source = inspect.getsource(service)

    assert "create_payment(" not in source
    assert "post_journal_entry(" not in source
    assert "execute_bank_transfer(" not in source
    assert "authorize_deduction(" not in source


def test_transaction_is_mandatory():
    with pytest.raises(
        service.RemittancePersistenceError,
        match="AsyncSession required",
    ):
        service._transaction(None)


def test_invalid_identifiers_rejected():
    for invalid in (
        0, -1, True, "1", None
    ):
        with pytest.raises(
            service.RemittancePersistenceError,
        ):
            service._id(invalid, "company_id")


def test_transaction_scoped_advisory_lock_present():
    source = inspect.getsource(service._lock)

    assert "pg_advisory_xact_lock" in source
    assert "company_id" in source
    assert "instruction_id" in source


def test_row_locking_present():
    source = inspect.getsource(service)

    assert source.count(".with_for_update()") >= 2


def test_persisted_reconciliation_required():
    source = inspect.getsource(
        service.append_persisted_remittance_event
    )

    assert "reconcile_remittance_plan(persisted)" in source
    assert "Persisted remittance balance mismatch" in source


def test_technical_event_replay():
    plan = open_remittance_plan(
        company_id=1,
        instruction_id=2,
        beneficiary_reference="TEST",
        currency_code="UAH",
        available_amount=Decimal("100.00"),
    )

    event = RemittanceEvent(
        event_id="e-1",
        company_id=1,
        instruction_id=2,
        beneficiary_reference="TEST",
        currency_code="UAH",
        amount=Decimal("30.00"),
    )

    first = append_remittance_event(
        plan=plan,
        event=event,
    )

    replay = append_remittance_event(
        plan=first.plan,
        event=event,
    )

    assert replay.duplicate is True
    assert replay.applied_now == Decimal("0.00")
    assert replay.balance.remaining_amount == Decimal("70.00")


def test_technical_overallocation_blocked():
    plan = open_remittance_plan(
        company_id=1,
        instruction_id=2,
        beneficiary_reference="TEST",
        currency_code="UAH",
        available_amount=Decimal("100.00"),
    )

    event = RemittanceEvent(
        event_id="e-over",
        company_id=1,
        instruction_id=2,
        beneficiary_reference="TEST",
        currency_code="UAH",
        amount=Decimal("101.00"),
    )

    with pytest.raises(
        RemittanceRuleError,
        match="exceeds available amount",
    ):
        append_remittance_event(
            plan=plan,
            event=event,
        )


def test_no_database_schema_change():
    # This service reuses C4-B schema.
    assert Path(
        "app/models/payroll_deduction_remittance.py"
    ).is_file()


def test_no_legal_authorization():
    from app.services.payroll_deduction_remittance_rules import (
        reconcile_remittance_plan,
    )

    plan = open_remittance_plan(
        company_id=1,
        instruction_id=2,
        beneficiary_reference="TEST",
        currency_code="UAH",
        available_amount=Decimal("100.00"),
    )

    balance = reconcile_remittance_plan(plan)

    assert balance.payment_execution_allowed is False
    assert balance.payroll_posting_allowed is False
    assert balance.legal_authorization == "NOT_AUTHORIZED"
