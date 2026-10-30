"""Install actual c070 trigger in the G7 isolated test schema."""
import ast
from pathlib import Path

import pytest_asyncio
from sqlalchemy import text

from test_employee_foundation import employee_engine


def _migration_ddl():
    path = Path(
        "alembic/versions/13d4a7b8c070_taxpayer_identity_review.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))

    upgrade = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "upgrade"
    ]
    if len(upgrade) != 1:
        raise AssertionError("Unexpected c070 upgrade")

    sql = []
    for node in ast.walk(upgrade[0]):
        if not isinstance(node, ast.Call):
            continue
        if not (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "op"
            and node.func.attr == "execute"
            and node.args
        ):
            continue
        try:
            value = ast.literal_eval(node.args[0])
        except (TypeError, ValueError):
            continue
        if isinstance(value, str):
            sql.append(value)

    result = []
    for marker in (
        "CREATE FUNCTION erp_taxpayer_review_immutable",
        "CREATE TRIGGER trg_taxpayer_review_immutable",
    ):
        matches = [item for item in sql if marker in item]
        if len(matches) != 1:
            raise AssertionError("Missing c070 DDL: " + marker)
        result.append(matches[0])

    return result


@pytest_asyncio.fixture
async def g7_engine(employee_engine):
    function_sql, trigger_sql = _migration_ddl()

    async with employee_engine.begin() as conn:
        schema = await conn.scalar(
            text("SELECT current_schema()")
        )

        if (
            not isinstance(schema, str)
            or not schema.startswith("test_employee_")
        ):
            raise AssertionError(
                "G7 DDL must run only in an isolated test schema"
            )

        # The connection already has the test schema search_path.
        # Creating the function here must not change public schema.
        await conn.execute(text(function_sql))
        await conn.execute(text(trigger_sql))

        count = await conn.scalar(
            text("""
                SELECT count(*)
                FROM pg_trigger
                WHERE tgrelid =
                    'payroll_taxpayer_identity_reviews'::regclass
                  AND tgname = 'trg_taxpayer_review_immutable'
                  AND tgenabled IN ('O', 'A')
            """)
        )

        if count != 1:
            raise AssertionError(
                "G7 immutability trigger not installed"
            )

        function_schema = await conn.scalar(
            text("""
                SELECT n.nspname
                FROM pg_proc p
                JOIN pg_namespace n
                  ON n.oid = p.pronamespace
                WHERE p.proname =
                    'erp_taxpayer_review_immutable'
                  AND n.nspname = current_schema()
            """)
        )

        if function_schema != schema:
            raise AssertionError(
                "G7 trigger function not owned by test schema"
            )

    yield employee_engine
