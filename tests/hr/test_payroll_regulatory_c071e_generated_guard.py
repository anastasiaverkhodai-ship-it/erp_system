
import ast
from pathlib import Path


TARGET = Path(
    "app/services/payroll_regulatory_report_service.py"
)


def test_generated_status_cannot_bypass_integrity():
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))

    functions = [
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "generate_payroll_regulatory_report"
    ]

    assert len(functions) == 1

    guards = [
        node for node in ast.walk(functions[0])
        if isinstance(node, ast.If)
        and "PayrollRegulatoryReportStatus.GENERATED.value"
        in ast.unparse(node.test)
    ]

    assert len(guards) == 1

    guard = guards[0]

    assert not any(
        isinstance(node, ast.Return)
        for statement in guard.body
        for node in ast.walk(statement)
    )

    assert any(
        isinstance(node, ast.Raise)
        for statement in guard.body
        for node in ast.walk(statement)
    )


def test_draft_and_normative_blockers_preserved():
    source = TARGET.read_text(encoding="utf-8")

    assert "PayrollRegulatoryReportStatus.DRAFT.value" in source
    assert "approved_mappings_for(" in source
    assert "bind_4df_financial_sources(" in source
    assert "no independently approved normative mappings" in source


def test_generated_status_requires_verified_integrity():
    source = TARGET.read_text(encoding="utf-8")

    assert (
        "does not establish verified statutory source integrity"
        in source
    )
