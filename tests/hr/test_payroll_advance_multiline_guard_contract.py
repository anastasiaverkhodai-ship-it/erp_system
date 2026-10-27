"""Execute the actual duplicate-component guard from advance clearing."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.payroll_advance_monthly_tax_clearing_db import (
    MonthlyTaxClearingError,
)

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "app/services/payroll_advance_monthly_tax_clearing_db.py"
)

PIT = "personal_income_tax"
LEVY = "military_levy"
USC = "unified_social_contribution"


def production_guard():
    text = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(text)

    matching = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        snippet = ast.get_source_segment(text, node) or ""
        if (
            "for line in statutory_lines:" in snippet
            and "seen_components.add(line.component)" in snippet
            and "Duplicate monthly statutory component" in snippet
        ):
            matching.append(node)

    assert len(matching) == 1, (
        "STOP: Advance clearing production guard changed"
    )

    loop = matching[0]
    statement = ast.Module(
        body=[
            ast.Assign(
                targets=[
                    ast.Name(id="seen_components", ctx=ast.Store())
                ],
                value=ast.Call(
                    func=ast.Name(id="set", ctx=ast.Load()),
                    args=[],
                    keywords=[],
                ),
            ),
            loop,
        ],
        type_ignores=[],
    )

    ast.fix_missing_locations(statement)
    return compile(statement, str(SOURCE), "exec")


def execute_guard(components):
    context = {
        "statutory_lines": [
            SimpleNamespace(component=c)
            for c in components
        ],
        "EMPLOYEE_COMPONENTS": frozenset({PIT, LEVY}),
        "MonthlyTaxClearingError": MonthlyTaxClearingError,
    }
    exec(production_guard(), context)
    return context["seen_components"]


@pytest.mark.parametrize("component", [PIT, LEVY])
def test_duplicate_employee_component_rejected(component):
    with pytest.raises(
        MonthlyTaxClearingError,
        match="Duplicate monthly statutory component",
    ):
        execute_guard([PIT, LEVY, component])


def test_single_lines_accepted():
    assert execute_guard([PIT, LEVY]) == {PIT, LEVY}


def test_usc_ignored_by_employee_withholding_guard():
    assert execute_guard([PIT, USC, LEVY]) == {PIT, LEVY}


def test_empty_lines_do_not_create_components():
    assert execute_guard([]) == set()


def test_duplicate_after_usc_still_rejected():
    with pytest.raises(
        MonthlyTaxClearingError,
        match="Duplicate monthly statutory component",
    ):
        execute_guard([PIT, USC, LEVY, PIT])
