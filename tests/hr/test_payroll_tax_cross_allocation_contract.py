"""Cross-service payroll tax journal ownership contract."""

import ast
from pathlib import Path


def _function_source(filename, function_name):
    source = Path(filename).read_text(encoding="utf-8")
    tree = ast.parse(source)
    matches = [
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == function_name
    ]
    assert len(matches) == 1
    return ast.get_source_segment(source, matches[0])


def test_monthly_registration_rejects_advance_owned_journal():
    code = _function_source(
        "app/services/payroll_tax_remittance_service.py",
        "register_tax_remittance",
    )
    assert "PayrollAdvanceTaxRemittance" in code
    assert "PayrollAdvanceTaxRemittance.company_id == company_id" in code
    assert "PayrollAdvanceTaxRemittance.journal_entry_id" in code

    lock = code.index(".with_for_update()")
    guard = code.index(
        "Payment journal already attributed to advance tax"
    )
    insert = code.index("row = Remittance(")

    assert lock < guard < insert


def test_advance_registration_rejects_monthly_owned_journal():
    code = _function_source(
        "app/services/payroll_advance_tax_remittance_service.py",
        "register_advance_tax_remittance",
    )
    assert "PayrollTaxRemittance" in code
    assert "PayrollTaxRemittance.company_id == company_id" in code
    assert "PayrollTaxRemittance.journal_entry_id == journal_entry_id" in code

    lock = code.index(".with_for_update()")
    guard = code.index(
        "Bank journal already allocated to monthly tax"
    )
    insert = code.index("row = PayrollAdvanceTaxRemittance(")

    assert lock < guard < insert
