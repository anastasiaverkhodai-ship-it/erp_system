from pathlib import Path

import app.models  # noqa: F401
from app.core.database import Base


EXPECTED_TABLES = {
    "vat_declarations",
    "vat_declaration_source_lines",
    "vat_declaration_carry_forward_lines",
    "vat_declaration_status_events",
}


def _checks(table_name: str) -> dict[str, str]:
    table = Base.metadata.tables[table_name]
    result = {}

    for constraint in table.constraints:
        name = getattr(constraint, "name", None)
        sqltext = getattr(constraint, "sqltext", None)

        if name and sqltext is not None:
            result[name] = str(sqltext)

    return result


def _uniques(table_name: str) -> set[str]:
    table = Base.metadata.tables[table_name]

    return {
        constraint.name
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
        and constraint.name
    }


def _foreign_keys(table_name: str) -> set[str]:
    table = Base.metadata.tables[table_name]

    return {
        constraint.name
        for constraint in table.constraints
        if constraint.__class__.__name__ == "ForeignKeyConstraint"
        and constraint.name
    }


def test_vat_declaration_tables_registered():
    assert EXPECTED_TABLES <= set(Base.metadata.tables)


def test_vat_declaration_header_columns():
    table = Base.metadata.tables["vat_declarations"]

    required = {
        "id",
        "company_id",
        "reporting_year",
        "reporting_month",
        "period_start",
        "period_end",
        "source_cutoff_at",
        "snapshot_version",
        "supersedes_declaration_id",
        "output_taxable_base",
        "output_vat",
        "input_taxable_base",
        "input_vat_credit",
        "opening_negative_carry",
        "vat_payable",
        "current_period_negative",
        "closing_negative_carry",
        "currency_code",
        "created_by",
        "created_at",
    }

    assert required <= set(table.c.keys())


def test_vat_declaration_header_constraints():
    checks = _checks("vat_declarations")

    assert "ck_vd_month" in checks
    assert "ck_vd_version" in checks
    assert "ck_vd_uah" in checks
    assert "ck_vd_amounts_nonneg" in checks
    assert "ck_vd_no_self_supersede" in checks

    uniques = _uniques("vat_declarations")
    assert "uq_vd_company_id" in uniques
    assert "uq_vd_period_version" in uniques

    fks = _foreign_keys("vat_declarations")
    assert "fk_vd_company" in fks
    assert "fk_vd_created_by" in fks
    assert "fk_vd_supersedes_tenant" in fks


def test_source_line_has_exact_typed_sources():
    table = Base.metadata.tables[
        "vat_declaration_source_lines"
    ]

    typed = {
        "tax_recognition_event_id",
        "sales_return_recognition_event_id",
        "trade_value_correction_event_id",
        "purchase_return_input_vat_credit_correction_event_id",
        "purchase_value_correction_input_vat_credit_correction_event_id",
    }

    assert typed <= set(table.c.keys())

    checks = _checks(
        "vat_declaration_source_lines"
    )

    assert "ck_vdsl_one_source" in checks
    assert "num_nonnulls" in checks["ck_vdsl_one_source"]
    assert "ck_vdsl_kind_fk" in checks
    assert "ck_vdsl_kind_dir" in checks


def test_source_line_signed_amounts_are_not_nonnegative_locked():
    checks = _checks(
        "vat_declaration_source_lines"
    )

    joined = "\n".join(checks.values()).lower()

    assert "taxable_base_delta >= 0" not in joined
    assert "tax_amount_delta >= 0" not in joined
    assert "ck_vdsl_nonzero" in checks


def test_source_line_source_kinds_locked():
    checks = _checks(
        "vat_declaration_source_lines"
    )
    text = checks["ck_vdsl_source_kind"]

    expected = {
        "output_tax_recognition",
        "input_tax_recognition",
        "sales_return",
        "sales_value_correction",
        "purchase_return_input_credit_correction",
        "purchase_value_input_credit_correction",
    }

    for value in expected:
        assert value in text


def test_source_line_uniqueness_is_snapshot_scoped():
    uniques = _uniques(
        "vat_declaration_source_lines"
    )

    expected = {
        "uq_vdsl_decl_line",
        "uq_vdsl_decl_taxrec",
        "uq_vdsl_decl_sret",
        "uq_vdsl_decl_tvc",
        "uq_vdsl_decl_pricc",
        "uq_vdsl_decl_pvicc",
    }

    assert expected <= uniques


def test_source_line_tenant_fks_present():
    fks = _foreign_keys(
        "vat_declaration_source_lines"
    )

    expected = {
        "fk_vdsl_decl_tenant",
        "fk_vdsl_taxrec_tenant",
        "fk_vdsl_sret_tenant",
        "fk_vdsl_tvc_tenant",
        "fk_vdsl_pricc_tenant",
        "fk_vdsl_pvicc_tenant",
    }

    assert expected <= fks


def test_carry_forward_contract():
    table = Base.metadata.tables[
        "vat_declaration_carry_forward_lines"
    ]

    required = {
        "source_declaration_id",
        "origin_reporting_year",
        "origin_reporting_month",
        "opening_amount",
        "consumed_amount",
        "closing_amount",
    }

    assert required <= set(table.c.keys())

    checks = _checks(
        "vat_declaration_carry_forward_lines"
    )

    assert "ck_vdcf_nonneg" in checks
    assert "ck_vdcf_consumed" in checks
    assert "ck_vdcf_arithmetic" in checks
    assert "ck_vdcf_not_self" in checks

    uniques = _uniques(
        "vat_declaration_carry_forward_lines"
    )
    assert "uq_vdcf_decl_origin" in uniques


def test_status_history_contract():
    table = Base.metadata.tables[
        "vat_declaration_status_events"
    ]

    assert {
        "status",
        "event_date",
        "reference",
        "created_by",
        "created_at",
    } <= set(table.c.keys())

    checks = _checks(
        "vat_declaration_status_events"
    )

    for value in (
        "prepared",
        "finalized",
        "submitted",
        "accepted",
        "rejected",
    ):
        assert value in checks["ck_vdse_status"]

    assert "ck_vdse_reference" in checks


def test_migration_revision_contract():
    path = Path(
        "alembic/versions/"
        "f7c8d9e0a142_add_vat_declaration_foundation.py"
    )

    text = path.read_text(
        encoding="utf-8"
    )

    assert 'revision: str = "f7c8d9e0a142"' in text
    assert '"e6b4a9c2d731"' in text

    for table_name in EXPECTED_TABLES:
        assert f'"{table_name}"' in text


def test_migration_does_not_touch_executed_vat_revisions():
    migration = Path(
        "alembic/versions/"
        "f7c8d9e0a142_add_vat_declaration_foundation.py"
    ).read_text(
        encoding="utf-8"
    )

    assert "op.create_table" in migration
    assert "op.alter_column" not in migration
    assert "op.drop_constraint" not in migration
