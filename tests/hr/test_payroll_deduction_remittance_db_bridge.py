"""D4-B2: database-backed remittance bridge contract tests."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.payroll_deduction_remittance_db_bridge as bridge

from app.services.payroll_deduction_remittance_reconciliation import (
    ReconciliationResult,
)

D = Decimal


def arguments(**changes):
    values = dict(
        company_id=7,
        instruction_id=11,
        legal_category="enforcement_other",
        rule_version="technical-test-rule",
        effective_on=date(2026, 10, 13),
        priority=1,
        requested_amount=D("40.00"),
        statutory_net=D("100.00"),
        global_aggregate_cap=D("50.00"),
    )
    values.update(changes)
    return values


def session(*, transaction=True):
    from sqlalchemy.ext.asyncio import AsyncSession

    db = MagicMock(spec=AsyncSession)
    db.in_transaction.return_value = transaction
    return db


@pytest.mark.asyncio
async def test_transaction_required_before_db_access():
    db = session(transaction=False)

    with pytest.raises(
        bridge.RemittanceDatabaseBridgeError,
        match="transaction",
    ):
        await bridge.evaluate_database_backed_remittance_readiness(
            db,
            **arguments(),
        )

    db.scalar.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"company_id": 0},
        {"company_id": True},
        {"instruction_id": -1},
        {"priority": -1},
        {"requested_amount": D("NaN")},
        {"requested_amount": D("0.001")},
        {"statutory_net": D("-1.00")},
        {"global_aggregate_cap": D("101.00")},
        {"effective_on": "2026-10-13"},
        {"rule_version": ""},
        {"legal_category": ""},
    ],
)
async def test_invalid_inputs_fail_before_reads(change):
    db = session()

    with pytest.raises(
        bridge.RemittanceDatabaseBridgeError
    ):
        await bridge.evaluate_database_backed_remittance_readiness(
            db,
            **arguments(**change),
        )

    db.scalar.assert_not_called()


@pytest.mark.asyncio
async def test_missing_instruction_fails_closed():
    db = session()
    db.scalar = AsyncMock(return_value=None)

    with pytest.raises(
        bridge.RemittanceDatabaseBridgeError,
        match="Instruction not found",
    ):
        await bridge.evaluate_database_backed_remittance_readiness(
            db,
            **arguments(),
        )


@pytest.mark.asyncio
async def test_unsupported_instruction_method_fails_closed():
    db = session()
    db.scalar = AsyncMock(
        return_value=SimpleNamespace(
            method="percentage",
            fixed_amount=None,
            currency_code="UAH",
        )
    )

    with pytest.raises(
        bridge.RemittanceDatabaseBridgeError,
        match="fixed-amount",
    ):
        await bridge.evaluate_database_backed_remittance_readiness(
            db,
            **arguments(),
        )


@pytest.mark.asyncio
async def test_invalid_fixed_amount_fails_closed():
    db = session()
    db.scalar = AsyncMock(
        return_value=SimpleNamespace(
            method="fixed",
            fixed_amount=D("NaN"),
            currency_code="UAH",
        )
    )

    with pytest.raises(
        bridge.RemittanceDatabaseBridgeError,
        match="fixed_amount",
    ):
        await bridge.evaluate_database_backed_remittance_readiness(
            db,
            **arguments(),
        )


def test_bridge_has_no_payment_or_posting_dependencies():
    import ast
    from pathlib import Path

    path = Path(bridge.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))

    forbidden = {
        "commit",
        "rollback",
        "flush",
        "add",
        "add_all",
        "delete",
        "post_journal_entry",
        "register_tax_remittance",
        "append_persisted_remittance_event",
        "open_persisted_remittance_plan",
    }

    names = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)

    assert not names.intersection(forbidden)


def test_bridge_imports_existing_reconciliation():
    assert callable(bridge.reconcile_remittance_sources)
    assert callable(bridge.evaluate_remittance_readiness)
    assert callable(bridge.preview_persisted_remainder)
    assert callable(bridge.read_persisted_remittance_balance)
    assert callable(
        bridge.evaluate_trusted_accounting_orchestration
    )
