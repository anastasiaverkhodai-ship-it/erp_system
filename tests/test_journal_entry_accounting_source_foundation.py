from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
)

from app.models.journal_entry import (
    JournalEntry,
)


def test_journal_entry_has_typed_business_sources():
    columns = {
        column.name
        for column
        in JournalEntry.__table__.columns
    }

    assert "document_id" in columns
    assert "payment_id" in columns

    assert (
        "payment_settlement_allocation_id"
        in columns
    )

    # Do not replace real FKs with weak
    # polymorphic source_type/source_id.
    assert "source_type" not in columns
    assert "source_id" not in columns


def test_payment_source_is_company_scoped_fk():
    constraints = {
        constraint.name: constraint
        for constraint
        in JournalEntry.__table__.constraints
        if isinstance(
            constraint,
            ForeignKeyConstraint,
        )
    }

    constraint = constraints[
        "fk_journal_entries_company_payment"
    ]

    assert tuple(
        column.name
        for column
        in constraint.columns
    ) == (
        "company_id",
        "payment_id",
    )

    targets = tuple(
        element.target_fullname
        for element
        in constraint.elements
    )

    assert targets == (
        "payments.company_id",
        "payments.id",
    )


def test_settlement_source_is_company_scoped_fk():
    constraints = {
        constraint.name: constraint
        for constraint
        in JournalEntry.__table__.constraints
        if isinstance(
            constraint,
            ForeignKeyConstraint,
        )
    }

    constraint = constraints[
        (
            "fk_journal_entries_company_"
            "payment_settlement_allocation"
        )
    ]

    assert tuple(
        column.name
        for column
        in constraint.columns
    ) == (
        "company_id",
        "payment_settlement_allocation_id",
    )

    targets = tuple(
        element.target_fullname
        for element
        in constraint.elements
    )

    assert targets == (
        (
            "payment_settlement_allocations."
            "company_id"
        ),
        (
            "payment_settlement_allocations."
            "id"
        ),
    )


def test_journal_entry_allows_at_most_one_business_source():
    constraints = {
        constraint.name: constraint
        for constraint
        in JournalEntry.__table__.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    constraint = constraints[
        (
            "ck_journal_entries_"
            "at_most_one_business_source"
        )
    ]

    sql = str(
        constraint.sqltext
    )

    assert "document_id" in sql
    assert "payment_id" in sql

    assert (
        "payment_settlement_allocation_id"
        in sql
    )


def test_original_payment_journal_entry_is_unique():
    indexes = {
        index.name: index
        for index
        in JournalEntry.__table__.indexes
    }

    index = indexes[
        "uq_journal_entry_original_payment"
    ]

    assert index.unique is True

    assert tuple(
        expression.name
        for expression
        in index.expressions
    ) == (
        "payment_id",
    )

    where = str(
        index.dialect_options[
            "postgresql"
        ]["where"]
    )

    assert (
        "reversal_of_id IS NULL"
        in where
    )

    assert (
        "payment_id IS NOT NULL"
        in where
    )


def test_original_settlement_journal_entry_is_unique():
    indexes = {
        index.name: index
        for index
        in JournalEntry.__table__.indexes
    }

    index = indexes[
        (
            "uq_journal_entry_original_"
            "payment_settlement_allocation"
        )
    ]

    assert index.unique is True

    assert tuple(
        expression.name
        for expression
        in index.expressions
    ) == (
        "payment_settlement_allocation_id",
    )

    where = str(
        index.dialect_options[
            "postgresql"
        ]["where"]
    )

    assert (
        "reversal_of_id IS NULL"
        in where
    )

    assert (
        "payment_settlement_allocation_id "
        "IS NOT NULL"
        in where
    )


def test_existing_document_source_uniqueness_remains():
    indexes = {
        index.name: index
        for index
        in JournalEntry.__table__.indexes
    }

    assert (
        "uq_journal_entry_original_document"
        in indexes
    )


def test_manual_journal_entry_remains_supported():
    entry = JournalEntry(
        company_id=1,
        document_id=None,
        payment_id=None,
        payment_settlement_allocation_id=None,
        accounting_rule_id=None,
        entry_date=__import__(
            "datetime"
        ).date.today(),
        created_by=1,
    )

    assert entry.document_id is None
    assert entry.payment_id is None

    assert (
        entry.payment_settlement_allocation_id
        is None
    )


def test_accounting_reversal_preserves_typed_source_ids():
    from pathlib import Path

    text = Path(
        "app/services/accounting_reversal.py"
    ).read_text()

    assert (
        "payment_id=original_entry.payment_id"
        in text
    )

    assert (
        "payment_settlement_allocation_id=("
        in text
    )

    assert (
        "original_entry."
        "payment_settlement_allocation_id"
        in text
    )
