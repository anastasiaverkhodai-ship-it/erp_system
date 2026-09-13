from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
)

from app.models.bank_statement_reconciliation import (
    BankStatementReconciliation,
    BankStatementReconciliationActiveLink,
)


def test_reconciliation_table_name():
    assert (
        BankStatementReconciliation.__tablename__
        == "bank_statement_reconciliations"
    )


def test_active_projection_table_name():
    assert (
        BankStatementReconciliationActiveLink.__tablename__
        == "bank_statement_reconciliation_active_links"
    )


def test_reconciliation_history_is_event_shaped():
    columns = {
        column.name
        for column
        in BankStatementReconciliation.__table__.columns
    }

    assert "reversal_of_id" in columns

    for forbidden in (
        "status",
        "reversed_by",
        "reversed_at",
    ):
        assert forbidden not in columns


def test_reversal_of_is_unique():
    constraints = (
        BankStatementReconciliation
        .__table__
        .constraints
    )

    uniques = {
        constraint.name
        for constraint in constraints
        if isinstance(
            constraint,
            UniqueConstraint,
        )
    }

    assert (
        "uq_bank_statement_reconciliations_reversal_of_id"
        in uniques
    )


def test_reversal_has_company_scoped_self_fk():
    table = (
        BankStatementReconciliation
        .__table__
    )

    targets = {
        tuple(
            element.target_fullname
            for element
            in constraint.elements
        )
        for constraint
        in table.constraints
        if isinstance(
            constraint,
            ForeignKeyConstraint,
        )
    }

    assert (
        "bank_statement_reconciliations.company_id",
        "bank_statement_reconciliations.id",
    ) in targets


def test_not_self_reversal_check_exists():
    names = {
        constraint.name
        for constraint
        in BankStatementReconciliation
        .__table__
        .constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    assert (
        "ck_bank_statement_reconciliations_not_self_reversal"
        in names
    )


def test_active_projection_one_line_unique():
    constraints = (
        BankStatementReconciliationActiveLink
        .__table__
        .constraints
    )

    unique_columns = {
        tuple(
            column.name
            for column
            in constraint.columns
        )
        for constraint
        in constraints
        if isinstance(
            constraint,
            UniqueConstraint,
        )
    }

    assert (
        "company_id",
        "bank_statement_line_id",
    ) in unique_columns


def test_active_projection_event_unique():
    constraints = (
        BankStatementReconciliationActiveLink
        .__table__
        .constraints
    )

    unique_columns = {
        tuple(
            column.name
            for column
            in constraint.columns
        )
        for constraint
        in constraints
        if isinstance(
            constraint,
            UniqueConstraint,
        )
    }

    assert (
        "company_id",
        "reconciliation_id",
    ) in unique_columns


def test_active_projection_has_company_scoped_fks():
    table = (
        BankStatementReconciliationActiveLink
        .__table__
    )

    targets = {
        tuple(
            element.target_fullname
            for element
            in constraint.elements
        )
        for constraint
        in table.constraints
        if isinstance(
            constraint,
            ForeignKeyConstraint,
        )
    }

    assert (
        "bank_statement_lines.company_id",
        "bank_statement_lines.id",
    ) in targets

    assert (
        "payments.company_id",
        "payments.id",
    ) in targets

    assert (
        "bank_statement_reconciliations.company_id",
        "bank_statement_reconciliations.id",
    ) in targets


def test_reconciliation_has_no_gl_provenance():
    columns = {
        column.name
        for column
        in BankStatementReconciliation
        .__table__
        .columns
    }

    assert "journal_entry_id" not in columns
    assert "accounting_rule_id" not in columns
