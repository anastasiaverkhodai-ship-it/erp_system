import importlib.util
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, ForeignKeyConstraint

from app.models.payroll_termination_severance import (
    PayrollTerminationSeverance,
)


MIGRATION_PATH = Path(
    "alembic/versions/"
    "13d4a7b8c040_payroll_termination_severance.py"
)


def test_single_expected_migration_head():
    script = ScriptDirectory.from_config(Config("alembic.ini"))

    assert script.get_heads() == ["13d4a7b8c063"]
    assert (
        script.get_revision("13d4a7b8c040").down_revision
        == "13d4a7b8c039"
    )


def test_severance_source_contract():
    table = PayrollTerminationSeverance.__table__

    assert table.name == "payroll_termination_severances"

    columns = set(table.columns.keys())

    assert {
        "company_id",
        "payroll_input_id",
        "employment_contract_id",
        "termination_event_id",
        "revision",
        "supersedes_id",
        "request_key",
        "policy_ground",
        "policy_category",
        "labour_code_provision",
        "source_reference",
        "basis_as_of",
        "basis_reference",
        "base_amount",
        "gross_amount",
        "created_by",
    } <= columns

    constraints = {
        item.name: item for item in table.constraints
        if item.name
    }

    assert "uq_pts_request" in constraints
    assert "uq_pts_event_revision" in constraints
    assert "uq_pts_supersedes" in constraints

    assert isinstance(
        constraints["fk_pts_event"],
        ForeignKeyConstraint,
    )

    assert isinstance(
        constraints["fk_pts_input"],
        ForeignKeyConstraint,
    )

    assert isinstance(
        constraints["ck_pts_category"],
        CheckConstraint,
    )

    check_text = str(
        constraints["ck_pts_category"].sqltext
    )

    assert "average_months" in check_text
    assert "minimum_wages" in check_text
    assert "manual_review" not in check_text


def test_migration_declares_immutable_source():
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "CREATE TRIGGER trg_payroll_severance_immutable" in source
    assert "BEFORE UPDATE OR DELETE" in source
    assert "Historical severance source is immutable" in source
    assert "Cannot drop documented severance history" in source

    assert "DROP TABLE IF EXISTS" not in source
    assert "CASCADE" not in source


def test_migration_compiles_without_executing_database_ddl():
    spec = importlib.util.spec_from_file_location(
        "severance_source_migration",
        MIGRATION_PATH,
    )

    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "13d4a7b8c040"
    assert module.down_revision == "13d4a7b8c039"
