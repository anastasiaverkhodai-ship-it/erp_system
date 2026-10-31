import ast
from pathlib import Path


PROJECTION = Path(
    "app/services/payroll_regulatory_4df_projection.py"
)

GENERATOR = Path(
    "app/services/payroll_regulatory_report_service.py"
)


def test_projection_preserves_dated_slice_count():
    tree = ast.parse(
        PROJECTION.read_text(encoding="utf-8")
    )

    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "project_4df_financial_groups"
    ]

    assert len(functions) == 1

    evidence_objects = []

    for node in ast.walk(functions[0]):
        if not isinstance(node, ast.Dict):
            continue

        keys = [
            key.value if isinstance(key, ast.Constant) else None
            for key in node.keys
        ]

        if (
            "source_sha256" in keys
            and "slice_number" in keys
        ):
            evidence_objects.append(node)

    assert len(evidence_objects) == 1

    evidence = evidence_objects[0]

    keys = [
        key.value if isinstance(key, ast.Constant) else None
        for key in evidence.keys
    ]

    assert "slice_count" in keys

    index = keys.index("slice_count")
    expression = evidence.values[index]

    assert isinstance(expression, ast.Subscript)
    assert isinstance(expression.value, ast.Name)
    assert expression.value.id == "payload"
    assert isinstance(expression.slice, ast.Constant)
    assert expression.slice.value == "slice_count"


def test_generator_calls_source_binding():
    text = GENERATOR.read_text(encoding="utf-8")

    assert text.count(
        "binding_manifest = bind_4df_financial_sources("
    ) == 1

    assert "project_4df_financial_groups(" in text


def test_repair_does_not_enable_statutory_filing():
    projection = PROJECTION.read_text(encoding="utf-8")
    generator = GENERATOR.read_text(encoding="utf-8")

    assert "REVIEW_ONLY_NOT_FOR_FILING" in projection
    assert '"statutory_approved": False' in projection

    assert (
        "independently approved statutory form"
        in generator
    )
