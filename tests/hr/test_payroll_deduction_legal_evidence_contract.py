"""ORM and migration contract for payroll deduction evidence."""

import importlib.util
from pathlib import Path

from sqlalchemy import Identity

from app.core.database import Base
import app.models  # noqa: F401


TABLE_NAME = "payroll_deduction_legal_evidence"

MIGRATION_PATH = Path(
    "alembic/versions/"
    "13d4a7b8c021_payroll_deduction_legal_evidence.py"
)


def test_model_is_registered():
    assert TABLE_NAME in Base.metadata.tables


def test_primary_key_uses_identity():
    table = Base.metadata.tables[TABLE_NAME]

    assert isinstance(table.c.id.identity, Identity)
    assert table.c.id.primary_key


def test_company_scoped_instruction_foreign_key():
    table = Base.metadata.tables[TABLE_NAME]

    foreign_keys = [
        constraint
        for constraint in table.foreign_key_constraints
    ]

    assert any(
        constraint.referred_table.name
        == "payroll_deduction_instructions"
        and set(constraint.column_keys)
        == {
            "company_id",
            "payroll_deduction_instruction_id",
        }
        for constraint in foreign_keys
    )


def test_constraint_names_fit_postgresql():
    table = Base.metadata.tables[TABLE_NAME]

    for constraint in table.constraints:
        if constraint.name:
            assert len(constraint.name.encode("utf-8")) <= 63


def test_evidence_columns_exist():
    table = Base.metadata.tables[TABLE_NAME]

    required = {
        "id",
        "company_id",
        "payroll_deduction_instruction_id",
        "legal_category",
        "document_reference",
        "verification_status",
        "verified_by",
        "verified_at",
        "created_by",
        "created_at",
    }

    assert required.issubset(table.columns.keys())


def test_migration_revision_chain():
    spec = importlib.util.spec_from_file_location(
        "payroll_deduction_evidence_migration",
        MIGRATION_PATH,
    )

    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "13d4a7b8c021"
    assert module.down_revision == "13d4a7b8c020"
