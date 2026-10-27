"""Direct execution of the production severance multiline guard."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


SOURCE = (
    Path(__file__).resolve().parents[2]
    / "app/services/payroll_severance_payment_service.py"
)

PIT = "personal_income_tax"
LEVY = "military_levy"
USC = "unified_social_contribution"


def execute_production_mapping(names):
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    found = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(t, ast.Name) and t.id == "components"
            for t in node.targets
        ):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        if node.value.keys:
            continue
        found.append(node)

    assert len(found) == 1, (
        "STOP: Production severance mapping changed"
    )

    assign = found[0]
    parent_list = None

    for node in ast.walk(tree):
        if not hasattr(node, "body"):
            continue
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        if assign in body:
            parent_list = body
            break

    assert parent_list is not None
    index = parent_list.index(assign)
    assert index + 1 < len(parent_list)

    loop = parent_list[index + 1]
    assert isinstance(loop, ast.For)
    assert isinstance(loop.target, ast.Name)
    assert loop.target.id == "line"
    assert isinstance(loop.iter, ast.Name)
    assert loop.iter.id == "lines"

    snippet = ast.get_source_segment(source, loop) or ""
    assert (
        "Severance statutory component has multiple lines"
        in snippet
    )

    module = ast.Module(
        body=[assign, loop],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)

    env = {
        "lines": [
            SimpleNamespace(component=name)
            for name in names
        ],
    }

    exec(compile(module, str(SOURCE), "exec"), env)
    return env["components"]


@pytest.mark.parametrize("component", [PIT, LEVY, USC])
def test_duplicate_component_rejected(component):
    with pytest.raises(
        ValueError,
        match="Severance statutory component has multiple lines",
    ):
        execute_production_mapping([PIT, LEVY, USC, component])


def test_single_line_components_preserved():
    result = execute_production_mapping([PIT, LEVY, USC])
    assert set(result) == {PIT, LEVY, USC}


def test_empty_input():
    assert execute_production_mapping([]) == {}


def test_reordered_input():
    assert set(execute_production_mapping([USC, LEVY, PIT])) == {
        PIT, LEVY, USC
    }


def test_nonadjacent_duplicate_rejected():
    with pytest.raises(
        ValueError,
        match="multiple lines",
    ):
        execute_production_mapping([PIT, LEVY, USC, PIT])
