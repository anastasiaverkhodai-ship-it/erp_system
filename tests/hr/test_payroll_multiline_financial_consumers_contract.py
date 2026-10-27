"""Regression contracts for statutory multiline financial consumers.

These are structural contracts, not positive dated-multiline DB E2E.
"""

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

SERVICES = {
    "accounting": "payroll_accounting_service.py",
    "remittance": "payroll_tax_remittance_service.py",
    "payslip": "payroll_payslip_service.py",
    "balances": "payroll_tax_balance_service.py",
}


def source_for(name):
    path = ROOT / "app/services" / SERVICES[name]
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    return ast.parse(text), text


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_financial_consumer_source_compiles(name):
    tree, source = source_for(name)
    compile(tree, SERVICES[name], "exec")
    assert "PayrollStatutoryResultLine" in source


@pytest.mark.parametrize(
    "name",
    ["accounting", "remittance"],
)
def test_tax_component_amounts_are_accumulated(name):
    tree, source = source_for(name)

    # Verify the established aggregation contract is still present.
    assert "amounts[line.component] +=" in source

    # Ensure the aggregation source still parses as executable Python.
    assert isinstance(tree, ast.Module)


def test_payslip_has_ordered_statutory_lines():
    _, source = source_for("payslip")
    assert "PayrollStatutoryResultLine.line_no" in source


def test_tax_balance_references_persisted_line_amounts():
    _, source = source_for("balances")
    assert "PayrollStatutoryResultLine.amount" in source


def test_severance_mapping_rejects_duplicates():
    path = (
        ROOT / "app/services/"
        "payroll_severance_payment_service.py"
    )
    source = path.read_text(encoding="utf-8")

    assert (
        "Severance statutory component has multiple lines"
        in source
    )

    assert (
        "components = {line.component: line for line in lines}"
        not in source
    )
