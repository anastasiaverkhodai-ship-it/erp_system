"""P13-B3-B3-C2: severance statutory snapshot persistence contract.

Tests only. No production migration execution is permitted here.
"""

import ast
from pathlib import Path

import pytest


MODEL = Path(
    "app/models/payroll_severance_statutory_record.py"
)

MIGRATION = Path(
    "alembic/versions/"
    "13d4a7b8c042_severance_statutory_snapshot.py"
)

TABLE = "payroll_severance_statutory_snapshots"


def model_source():
    assert MODEL.is_file(), "ORM model must exist"
    source = MODEL.read_text(encoding="utf-8")
    ast.parse(source)
    return source


def migration_source():
    assert MIGRATION.is_file(), "Migration must exist"
    source = MIGRATION.read_text(encoding="utf-8")
    ast.parse(source)
    return source


def test_snapshot_model_declares_required_fields():
    source = model_source()

    required = (
        TABLE,
        "company_id",
        "payroll_calculation_id",
        "income_date",
        "currency_code",
        "ordinary_gross",
        "severance_gross",
        "total_gross",
        "severance_source_ids",
        "policy_code",
        "policy_valid_from",
        "policy_valid_to",
        "source_reference",
        "income_code_4df",
        "personal_income_tax_base",
        "military_levy_base",
        "unified_social_contribution_base",
        "fingerprint_sha256",
    )

    for name in required:
        assert name in source, name


def test_migration_follows_exact_current_head():
    source = migration_source()

    tree = ast.parse(source)
    values = {}

    for node in tree.body:
        if not isinstance(
            node, (ast.Assign, ast.AnnAssign)
        ):
            continue

        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
        )

        for target in targets:
            if isinstance(target, ast.Name):
                if target.id in {
                    "revision",
                    "down_revision",
                }:
                    values[target.id] = ast.literal_eval(
                        node.value
                    )

    assert values["revision"] == "13d4a7b8c042"
    assert values["down_revision"] == "13d4a7b8c041"


def test_company_scoped_fk_and_unique_constraint():
    source = migration_source()

    assert "payroll_calculations" in source
    assert "payroll_calculation_id" in source
    assert "company_id" in source
    assert "ForeignKeyConstraint" in source
    assert "UniqueConstraint" in source


def test_database_immutability_is_declared():
    source = migration_source().upper()

    assert "CREATE FUNCTION PAYROLL_SEVERANCE_SNAPSHOT_VALIDATE" in source
    assert "CREATE TRIGGER TRG_PSSS_VALIDATE" in source

    # A combined INSERT/UPDATE/DELETE trigger is valid provided
    # that its function rejects everything except INSERT.
    assert "BEFORE INSERT OR UPDATE OR DELETE" in source
    assert "IF TG_OP <> 'INSERT' THEN" in source
    assert "RAISE EXCEPTION" in source

    assert "CREATE TRIGGER TRG_PSSS_NO_TRUNCATE" in source
    assert "BEFORE TRUNCATE" in source
    assert "PAYROLL_SEVERANCE_SNAPSHOT_NO_TRUNCATE" in source


def test_snapshot_values_have_database_constraints():
    source = migration_source()

    assert "CheckConstraint" in source
    assert "fingerprint_sha256" in source
    assert "severance_gross" in source
    assert "income_code_4df" in source


def test_documented_source_provenance_is_enforced():
    source = migration_source()

    assert "severance_source_ids" in source
    assert "payroll_calculation_lines" in source
    assert "source_termination_severance_id" in source
    assert "line_type" in source
