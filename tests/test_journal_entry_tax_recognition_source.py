import inspect
from itertools import combinations

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
)

from app.models.journal_entry import (
    JournalEntry,
)
import app.services.accounting_reversal as accounting_reversal


def test_tax_recognition_source_column_exists():
    table = JournalEntry.__table__

    column = table.c.tax_recognition_event_id

    assert column.nullable is True

    assert (
        "ix_journal_entries_"
        "tax_recognition_event_id"
        in {
            index.name
            for index
            in table.indexes
        }
    )


def test_tax_recognition_source_has_company_scoped_fk():
    table = JournalEntry.__table__

    matches = []

    for constraint in table.constraints:
        if not isinstance(
            constraint,
            ForeignKeyConstraint,
        ):
            continue

        columns = tuple(
            column.name
            for column
            in constraint.columns
        )

        if columns == (
            "company_id",
            "tax_recognition_event_id",
        ):
            matches.append(
                constraint
            )

    assert len(matches) == 1

    targets = tuple(
        element.target_fullname
        for element
        in matches[0].elements
    )

    assert targets == (
        "tax_recognition_events.company_id",
        "tax_recognition_events.id",
    )


def test_only_one_original_je_per_tax_recognition_event():
    table = JournalEntry.__table__

    index = next(
        index
        for index
        in table.indexes
        if (
            index.name
            == (
                "uq_journal_entry_original_"
                "tax_recognition_event"
            )
        )
    )

    assert index.unique is True

    columns = tuple(
        column.name
        for column
        in index.columns
    )

    assert columns == (
        "tax_recognition_event_id",
    )

    where = str(
        index.dialect_options[
            "postgresql"
        ][
            "where"
        ]
    )

    assert (
        "reversal_of_id IS NULL"
        in where
    )

    assert (
        "tax_recognition_event_id "
        "IS NOT NULL"
        in where
    )


def test_business_source_constraint_covers_all_four_sources():
    table = JournalEntry.__table__

    constraint = next(
        constraint
        for constraint
        in table.constraints
        if (
            isinstance(
                constraint,
                CheckConstraint,
            )
            and constraint.name
            == (
                "ck_journal_entries_"
                "at_most_one_business_source"
            )
        )
    )

    sql = " ".join(
        str(
            constraint.sqltext
        ).split()
    )

    sources = (
        "document_id",
        "payment_id",
        "payment_settlement_allocation_id",
        "tax_recognition_event_id",
    )

    for left, right in combinations(
        sources,
        2,
    ):
        assert (
            f"{left} IS NULL "
            f"OR {right} IS NULL"
            in sql
        )


def test_accounting_reversal_preserves_tax_recognition_source():
    source = inspect.getsource(
        accounting_reversal
        .reverse_journal_entry
    )

    assert (
        "tax_recognition_event_id"
        in source
    )

    assert (
        "original_entry."
        "tax_recognition_event_id"
        in source
    )
