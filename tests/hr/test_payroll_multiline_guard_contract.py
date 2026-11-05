"""Validate the real fail-closed payroll guards without
moving asynchronous production statements outside their functions.

The tests execute the original AST conditions and original raise
statements. PostgreSQL integration remains a separate gate.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

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

RECONCILIATION_MESSAGE = (
    "Sick-pay ERP history requires reconciled assessed USC"
)


def _find_guard(source, tree, variable, marker):
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
            and len(condition.ops) == 1
            and isinstance(condition.ops[0], ast.Gt)
            and len(condition.comparators) == 1
            and isinstance(condition.comparators[0], ast.Constant)
            and condition.comparators[0].value == 1
        ):
            continue

        if marker not in (
            ast.get_source_segment(source, node) or ""
        ):
            continue

        matches.append(node)

    assert len(matches) == 1, (
        "STOP: Exact production cardinality guard missing"
    )

    return matches[0]


def _next_statement(tree, guard):
    followers = []

    for parent in ast.walk(tree):
        for _, statements in ast.iter_fields(parent):
            if not isinstance(statements, list):
                continue

            for index, statement in enumerate(statements):
                if statement is guard:
                    followers.append(
                        statements[index + 1:index + 2]
                    )

    assert len(followers) == 1, (
        "STOP: Production guard has no unique parent block"
    )
    assert len(followers[0]) == 1, (
        "STOP: Statement following guard missing"
    )

    return followers[0][0]


def _original_raise(source, node, marker):
    matches = [
        item
        for item in ast.walk(node)
        if isinstance(item, ast.Raise)
        and marker in (
            ast.get_source_segment(source, item) or ""
        )
    ]

    assert len(matches) == 1, (
        "STOP: Exact production fail-closed raise missing"
    )

    return matches[0]


def _isolated_guard(condition, raise_node, filename):
    # Only the actual production predicate and production raise
    # are compiled. Other statements in the production block
    # can contain await and must remain in their async function.
    isolated = ast.If(
        test=condition,
        body=[raise_node],
        orelse=[],
    )

    module = ast.Module(
        body=[isolated],
        type_ignores=[],
    )

    ast.fix_missing_locations(module)
    return compile(module, filename, "exec")


def load_actual_guards(filename, variable, marker):
    source = (ROOT / filename).read_text(encoding="utf-8")
    tree = ast.parse(source)

    guard = _find_guard(
        source,
        tree,
        variable,
        marker,
    )

    guard_raise = _original_raise(
        source,
        guard,
        marker,
    )

    cardinality = _isolated_guard(
        guard.test,
        guard_raise,
        filename,
    )

    follower = _next_statement(tree, guard)

    if variable == "tax":
        assert isinstance(follower, ast.If), (
            "STOP: Sick-history reconciliation guard missing"
        )

        expression = (
            ast.get_source_segment(source, follower.test)
            or ""
        )

        assert (
            "not tax" in expression
            and "header.gross_amount" in expression
            and "calculation.gross_amount" in expression
        ), "STOP: Sick-history reconciliation predicate changed"

        reconciliation_raise = _original_raise(
            source,
            follower,
            RECONCILIATION_MESSAGE,
        )

        reconciliation = _isolated_guard(
            follower.test,
            reconciliation_raise,
            filename,
        )

        return cardinality, reconciliation, None

    assert (
        isinstance(follower, ast.Assign)
        and len(follower.targets) == 1
        and isinstance(follower.targets[0], ast.Name)
        and follower.targets[0].id == variable
        and isinstance(follower.value, ast.IfExp)
    ), "STOP: Employee-peer post-guard selection missing"

    selection_module = ast.Module(
        body=[follower],
        type_ignores=[],
    )

    ast.fix_missing_locations(selection_module)

    selection = compile(
        selection_module,
        filename,
        "exec",
    )

    return cardinality, None, selection


@pytest.mark.parametrize(
    "filename,variable,marker",
    CASES,
    ids=["employee-peer", "sick-history"],
)
@pytest.mark.parametrize("count", [0, 1, 2])
def test_exact_production_guard(
    filename, variable, marker, count
):
    cardinality, reconciliation, selection = (
        load_actual_guards(filename, variable, marker)
    )

    class GuardError(ValueError):
        pass

    if variable == "tax":
        rows = [
            (SimpleNamespace(gross_amount=100), None)
            for _ in range(count)
        ]
    else:
        rows = [object() for _ in range(count)]

    namespace = {
        variable: rows,
        "Error": GuardError,
        "PayrollAverageHistoryError": GuardError,
        "calculation": SimpleNamespace(gross_amount=100),
    }

    if count == 2:
        with pytest.raises(GuardError, match=marker):
            exec(cardinality, namespace)
        return

    exec(cardinality, namespace)

    if variable == "tax":
        if count == 0:
            with pytest.raises(
                GuardError,
                match=RECONCILIATION_MESSAGE,
            ):
                exec(reconciliation, namespace)
        else:
            exec(reconciliation, namespace)
            assert namespace[variable] is rows
        return

    exec(selection, namespace)

    expected = rows[0] if rows else None
    assert namespace[variable] is expected


def test_sick_history_rejects_gross_mismatch():
    filename, variable, marker = CASES[1]

    cardinality, reconciliation, selection = (
        load_actual_guards(filename, variable, marker)
    )

    assert selection is None

    class GuardError(ValueError):
        pass

    rows = [
        (SimpleNamespace(gross_amount=99), None)
    ]

    namespace = {
        variable: rows,
        "calculation": SimpleNamespace(gross_amount=100),
        "PayrollAverageHistoryError": GuardError,
        "Error": GuardError,
    }

    exec(cardinality, namespace)

    with pytest.raises(
        GuardError,
        match=RECONCILIATION_MESSAGE,
    ):
        exec(reconciliation, namespace)
