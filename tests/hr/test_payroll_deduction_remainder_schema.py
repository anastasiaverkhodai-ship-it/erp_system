"""Phase 13 C3-B2 schema contract tests.

These tests are offline and do not execute migrations.
"""

from pathlib import Path

import sqlalchemy as sa

from app.core.database import Base

from app.models.payroll_deduction_remainder import (
    PayrollDeductionRemainder,
    PayrollDeductionRemainderEvent,
)


def test_tables_registered():
    assert (
        PayrollDeductionRemainder.__table__
        is Base.metadata.tables[
            "payroll_deduction_remainders"
        ]
    )

    assert (
        PayrollDeductionRemainderEvent.__table__
        is Base.metadata.tables[
            "payroll_deduction_remainder_events"
        ]
    )


def test_company_scoped_instruction_fk():
    table = PayrollDeductionRemainder.__table__

    constraints = [
        value
        for value in table.constraints
        if isinstance(
            value,
            sa.ForeignKeyConstraint,
        )
    ]

    fk = next(
        value
        for value in constraints
        if value.name == "fk_pdr_company_instruction"
    )

    assert [
        element.parent.name
        for element in fk.elements
    ] == ["company_id", "instruction_id"]

    assert [
        element.target_fullname
        for element in fk.elements
    ] == [
        "payroll_deduction_instructions.company_id",
        "payroll_deduction_instructions.id",
    ]

    assert fk.ondelete == "RESTRICT"


def test_company_scoped_event_fk():
    table = PayrollDeductionRemainderEvent.__table__

    fk = next(
        value
        for value in table.constraints
        if isinstance(
            value,
            sa.ForeignKeyConstraint,
        )
        and value.name == "fk_pdre_company_remainder"
    )

    assert [
        element.target_fullname
        for element in fk.elements
    ] == [
        "payroll_deduction_remainders.company_id",
        "payroll_deduction_remainders.id",
    ]

    assert fk.ondelete == "RESTRICT"


def test_unique_constraints():
    remainder = PayrollDeductionRemainder.__table__
    event = PayrollDeductionRemainderEvent.__table__

    remainder_names = {
        constraint.name
        for constraint in remainder.constraints
        if isinstance(
            constraint,
            sa.UniqueConstraint,
        )
    }

    event_names = {
        constraint.name
        for constraint in event.constraints
        if isinstance(
            constraint,
            sa.UniqueConstraint,
        )
    }

    assert "uq_pdr_company_instruction" in remainder_names
    assert "uq_pdr_company_id_id" in remainder_names

    assert "uq_pdre_company_event" in event_names
    assert "uq_pdre_company_id_id" in event_names


def test_amounts_use_decimal_numeric():
    remainder = PayrollDeductionRemainder.__table__
    event = PayrollDeductionRemainderEvent.__table__

    original = remainder.c.original_amount.type
    amount = event.c.amount.type

    for value in (original, amount):
        assert isinstance(value, sa.Numeric)
        assert value.precision == 18
        assert value.scale == 2


def test_required_fields_and_currency():
    remainder = PayrollDeductionRemainder.__table__
    event = PayrollDeductionRemainderEvent.__table__

    for column in (
        remainder.c.company_id,
        remainder.c.instruction_id,
        remainder.c.original_amount,
        remainder.c.currency_code,
        remainder.c.created_by,
        remainder.c.created_at,
        event.c.company_id,
        event.c.remainder_id,
        event.c.event_id,
        event.c.amount,
        event.c.created_by,
        event.c.created_at,
    ):
        assert column.nullable is False

    assert remainder.c.currency_code.type.length == 3
    assert event.c.event_id.type.length == 100


def test_check_constraints():
    remainder = PayrollDeductionRemainder.__table__
    event = PayrollDeductionRemainderEvent.__table__

    remainder_checks = {
        value.name
        for value in remainder.constraints
        if isinstance(
            value,
            sa.CheckConstraint,
        )
    }

    event_checks = {
        value.name
        for value in event.constraints
        if isinstance(
            value,
            sa.CheckConstraint,
        )
    }

    assert "ck_pdr_original_nonnegative" in remainder_checks
    assert "ck_pdr_currency" in remainder_checks

    assert "ck_pdre_positive_amount" in event_checks
    assert "ck_pdre_event_id" in event_checks


def test_event_lookup_index():
    table = PayrollDeductionRemainderEvent.__table__

    indexes = {
        index.name: index
        for index in table.indexes
    }

    index = indexes[
        "ix_pdre_company_remainder_created"
    ]

    assert [
        column.name
        for column in index.columns
    ] == [
        "company_id",
        "remainder_id",
        "created_at",
        "id",
    ]


def test_postgresql_ddl_compiles():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    dialect = postgresql.dialect()

    for table in (
        PayrollDeductionRemainder.__table__,
        PayrollDeductionRemainderEvent.__table__,
    ):
        compiled = str(
            CreateTable(table).compile(
                dialect=dialect,
            )
        )

        assert "CREATE TABLE" in compiled
        assert "FOREIGN KEY" in compiled
        assert "CHECK" in compiled


def test_migration_contract():
    path = Path(
        "alembic/versions/"
        "13d4a7b8c023_payroll_deduction_remainder_ledger.py"
    )

    source = path.read_text(encoding="utf-8")

    assert 'revision = "13d4a7b8c023"' in source
    assert 'down_revision = "13d4a7b8c022"' in source

    assert source.count("op.create_table(") == 2
    assert source.count("op.drop_table(") == 2

    assert "fk_pdr_company_instruction" in source
    assert "fk_pdre_company_remainder" in source

    assert "uq_pdr_company_instruction" in source
    assert "uq_pdre_company_event" in source

    assert "pdr_prevent_history_mutation" in source

    assert "BEFORE UPDATE OR DELETE" in source
    assert "BEFORE TRUNCATE" in source


def test_no_payroll_posting_activation():
    paths = (
        Path(
            "app/models/payroll_deduction_remainder.py"
        ),
        Path(
            "alembic/versions/"
            "13d4a7b8c023_payroll_deduction_remainder_ledger.py"
        ),
    )

    for path in paths:
        source = path.read_text(encoding="utf-8")

        assert "db.commit(" not in source
        assert "generate_and_post_payroll_deduction_journal(" not in source
