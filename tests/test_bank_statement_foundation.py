from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
)

from app.models.bank_statement import BankStatement
from app.models.bank_statement_line import (
    BankStatementLine,
)


def test_bank_statement_columns():
    assert {
        column.name
        for column
        in BankStatement.__table__.columns
    } == {
        "id",
        "company_id",
        "bank_account_id",
        "external_id",
        "statement_date",
        "period_start",
        "period_end",
        "source_type",
        "source_reference",
        "created_by",
        "created_at",
    }


def test_bank_statement_identity_constraints():
    uniques = {
        tuple(
            column.name
            for column
            in constraint.columns
        )
        for constraint
        in BankStatement.__table__.constraints
        if isinstance(
            constraint,
            UniqueConstraint,
        )
    }

    assert (
        "company_id",
        "id",
    ) in uniques

    assert (
        "company_id",
        "bank_account_id",
        "external_id",
    ) in uniques


def test_bank_statement_company_safe_bank_account_fk():
    found = False

    for constraint in (
        BankStatement.__table__.constraints
    ):
        if not isinstance(
            constraint,
            ForeignKeyConstraint,
        ):
            continue

        local = tuple(
            element.parent.name
            for element
            in constraint.elements
        )
        remote = tuple(
            element.target_fullname
            for element
            in constraint.elements
        )

        if (
            local
            == (
                "company_id",
                "bank_account_id",
            )
            and remote
            == (
                "bank_accounts.company_id",
                "bank_accounts.id",
            )
        ):
            found = True

    assert found


def test_bank_statement_line_columns():
    assert {
        column.name
        for column
        in BankStatementLine.__table__.columns
    } == {
        "id",
        "company_id",
        "bank_statement_id",
        "bank_account_id",
        "external_line_id",
        "transaction_date",
        "value_date",
        "amount",
        "currency_code",
        "counterparty_name",
        "counterparty_account",
        "payment_reference",
        "description",
        "raw_payload",
        "created_at",
    }


def test_bank_statement_line_identity_constraint():
    uniques = {
        tuple(
            column.name
            for column
            in constraint.columns
        )
        for constraint
        in BankStatementLine.__table__.constraints
        if isinstance(
            constraint,
            UniqueConstraint,
        )
    }

    assert (
        "company_id",
        "bank_statement_id",
        "external_line_id",
    ) in uniques


def test_bank_statement_line_statement_identity_fk():
    found = False

    for constraint in (
        BankStatementLine.__table__.constraints
    ):
        if not isinstance(
            constraint,
            ForeignKeyConstraint,
        ):
            continue

        local = tuple(
            element.parent.name
            for element
            in constraint.elements
        )
        remote = tuple(
            element.target_fullname
            for element
            in constraint.elements
        )

        if (
            local
            == (
                "company_id",
                "bank_statement_id",
                "bank_account_id",
            )
            and remote
            == (
                "bank_statements.company_id",
                "bank_statements.id",
                "bank_statements.bank_account_id",
            )
        ):
            found = True

    assert found


def test_statement_line_checks():
    names = {
        constraint.name
        for constraint
        in BankStatementLine.__table__.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    assert (
        "ck_bank_statement_lines_amount_nonzero"
        in names
    )

    assert (
        "ck_bank_statement_lines_currency_code_length"
        in names
    )
