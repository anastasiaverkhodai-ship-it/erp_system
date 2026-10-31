import ast
from pathlib import Path


TARGET = Path(
    "app/services/payroll_regulatory_report_service.py"
)


def generator_function():
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))

    functions = [
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "generate_payroll_regulatory_report"
    ]

    assert len(functions) == 1
    return functions[0]


def test_source_binding_is_in_actual_generator():
    function = generator_function()

    calls = [
        node.func.id
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
    ]

    for required in (
        "validate_4df_financial_contract",
        "project_4df_financial_groups",
        "bind_4df_financial_sources",
    ):
        assert required in calls


def test_official_generation_remains_blocked():
    function = generator_function()

    messages = [
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
    ]

    assert any(
        "independently approved statutory form" in message
        for message in messages
    )

    assert any(
        "no independently approved normative mappings" in message
        for message in messages
    )


def test_binding_executed_before_filing_blocker():
    source = TARGET.read_text(encoding="utf-8")
    function = generator_function()

    segment = ast.get_source_segment(source, function)

    assert segment is not None

    binding = segment.index(
        "binding_manifest = bind_4df_financial_sources("
    )

    blocker = segment.index(
        '"independently approved statutory form "'
    )

    assert binding < blocker
