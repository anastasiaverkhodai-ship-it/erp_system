"""Test the actual fail-closed AST guards from production.

These checks execute the exact guard and selection statements.
Database query integration remains a separate PostgreSQL gate.
"""

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

CASES = (
    (
        "app/services/payroll_employee_tax_base_service.py",
        "saved",
        "Multiple peer statutory tax lines require dated employee-wide reconciliation",
    ),
    (
        "app/services/payroll_average_history_service.py",
        "tax",
        "Multiple dated USC lines require attributed sick-pay income allocation",
    ),
)


def load_actual_guard(filename, variable, marker):
    source = (ROOT / filename).read_text(encoding="utf-8")
    tree = ast.parse(source)

    matches = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue

        condition = node.test

        if not isinstance(condition, ast.Compare):
            continue

        if not isinstance(condition.left, ast.Call):
            continue

        call = condition.left

        if not (
            isinstance(call.func, ast.Name)
            and call.func.id == "len"
            and len(call.args) == 1
            and isinstance(call.args[0], ast.Name)
            and call.args[0].id == variable
        ):
            continue

        if not (
            len(condition.ops) == 1
            and isinstance(condition.ops[0], ast.Gt)
            and len(condition.comparators) == 1
            and isinstance(condition.comparators[0], ast.Constant)
            and condition.comparators[0].value == 1
        ):
            continue

        if marker not in (ast.get_source_segment(source, node) or ""):
            continue

        matches.append(node)

    assert len(matches) == 1, "STOP: Exact production guard missing"

    guard = matches[0]

    selections = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == variable
        and node.lineno == guard.end_lineno + 1
        and isinstance(node.value, ast.IfExp)
    ]

    assert len(selections) == 1, (
        "STOP: Exact post-guard selection missing"
    )

    module = ast.Module(
        body=[guard, selections[0]],
        type_ignores=[],
    )

    ast.fix_missing_locations(module)
    return compile(module, filename, "exec")


@pytest.mark.parametrize(
    "filename,variable,marker",
    CASES,
    ids=["employee-peer", "sick-history"],
)
@pytest.mark.parametrize("count", [0, 1, 2])
def test_exact_production_guard(filename, variable, marker, count):
    executable = load_actual_guard(filename, variable, marker)

    class GuardError(ValueError):
        pass

    rows = [object() for _ in range(count)]

    namespace = {
        variable: rows,
        "Error": GuardError,
        "PayrollAverageHistoryError": GuardError,
    }

    if count == 2:
        with pytest.raises(GuardError, match=marker):
            exec(executable, namespace)
        return

    exec(executable, namespace)

    expected = rows[0] if rows else None

    assert namespace[variable] is expected
