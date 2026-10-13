"""D3-B1: preserve the payroll deduction accounting safety boundary.

These checks do not authorize posting or change accounting behavior.
"""

import ast
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.payroll_deduction_result_service import (
    PayrollDeductionResultConflictError,
    require_authorized_deduction_result,
)


ROOT = Path(__file__).resolve().parents[2]

ACCOUNTING = (
    ROOT / "app/services/payroll_deduction_accounting_service.py"
)

API = ROOT / "app/api/v1/payroll_deductions.py"


def function_node(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))

    matches = [
        node for node in tree.body
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        )
        and node.name == name
    ]

    assert len(matches) == 1
    return matches[0]


def calls_to(node, method):
    return [
        item for item in ast.walk(node)
        if isinstance(item, ast.Call)
        and (
            (
                isinstance(item.func, ast.Name)
                and item.func.id == method
            )
            or (
                isinstance(item.func, ast.Attribute)
                and item.func.attr == method
            )
        )
    ]


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("0.01"),
        Decimal("1.00"),
        Decimal("100.00"),
        Decimal("999999.99"),
    ],
)
def test_positive_amount_never_authorizes_posting(amount):
    result = SimpleNamespace(deduction_amount=amount)

    with pytest.raises(PayrollDeductionResultConflictError):
        require_authorized_deduction_result(result)


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("-0.01"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        None,
        "not-a-number",
    ],
)
def test_invalid_amount_never_authorizes_posting(amount):
    result = SimpleNamespace(deduction_amount=amount)

    with pytest.raises(PayrollDeductionResultConflictError):
        require_authorized_deduction_result(result)


def test_zero_amount_passes_guard_but_does_not_mean_posting():
    result = SimpleNamespace(
        deduction_amount=Decimal("0.00")
    )

    assert require_authorized_deduction_result(result) is None


def test_accounting_guard_precedes_any_journal_write():
    node = function_node(
        ACCOUNTING,
        "generate_and_post_payroll_deduction_journal",
    )

    guard = calls_to(
        node,
        "require_authorized_deduction_result",
    )

    assert len(guard) == 1

    posting = calls_to(node, "post_journal_entry")
    additions = calls_to(node, "add")
    flushes = calls_to(node, "flush")

    assert posting, "Journal posting call missing"
    assert additions, "Journal entry insertion call missing"
    assert flushes, "Journal entry flush call missing"

    first_effect = min(
        call.lineno
        for call in posting + additions + flushes
    )

    assert guard[0].lineno < first_effect, (
        "Authorization guard must precede all journal writes"
    )


def test_accounting_guard_precedes_existing_journal_lookup():
    node = function_node(
        ACCOUNTING,
        "generate_and_post_payroll_deduction_journal",
    )

    guard = calls_to(
        node,
        "require_authorized_deduction_result",
    )
    existing = calls_to(
        node,
        "get_payroll_deduction_journal",
    )

    assert len(guard) == 1
    assert len(existing) == 1

    assert guard[0].lineno < existing[0].lineno, (
        "Existing journals must not bypass authorization"
    )


def test_accounting_service_does_not_own_commit():
    node = function_node(
        ACCOUNTING,
        "generate_and_post_payroll_deduction_journal",
    )

    assert not calls_to(node, "commit")
    assert not calls_to(node, "rollback")


def test_api_owns_accounting_transaction():
    node = function_node(
        API,
        "api_post_payroll_deduction_journal",
    )

    assert calls_to(node, "commit")
    assert calls_to(node, "rollback")


def test_reversal_uses_existing_accounting_reversal_service():
    node = function_node(
        ACCOUNTING,
        "reverse_payroll_deduction_journal",
    )

    assert calls_to(node, "get_payroll_deduction_journal")
    assert calls_to(node, "reverse_journal_entry")

    assert not calls_to(node, "commit")


def test_no_authorization_bypass_added_to_accounting_api():
    node = function_node(
        API,
        "api_post_payroll_deduction_journal",
    )

    calls = calls_to(
        node,
        "generate_and_post_payroll_deduction_journal",
    )

    assert len(calls) == 1
