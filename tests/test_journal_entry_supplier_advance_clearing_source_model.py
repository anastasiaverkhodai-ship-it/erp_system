from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
)

from app.models.journal_entry import (
    JournalEntry,
)


SOURCE = (
    "supplier_advance_clearing_event_id"
)

OTHER_SOURCES = (
    "document_id",
    "payment_id",
    "payment_settlement_allocation_id",
    "tax_recognition_event_id",
    "sales_recognition_event_id",
    "vat_advance_bridge_event_id",
    "input_vat_fulfillment_bridge_event_id",
)


def test_supplier_source_column_exists():
    assert (
        SOURCE
        in JournalEntry.__table__.c
    )

    column = (
        JournalEntry.__table__.c[
            SOURCE
        ]
    )

    assert column.nullable is True


def test_supplier_source_company_fk():
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
            "supplier_advance_clearing_event"
        )
    ]

    assert tuple(
        element.parent.name
        for element
        in constraint.elements
    ) == (
        "company_id",
        SOURCE,
    )

    assert tuple(
        element.target_fullname
        for element
        in constraint.elements
    ) == (
        (
            "supplier_advance_clearing_events."
            "company_id"
        ),
        (
            "supplier_advance_clearing_events."
            "id"
        ),
    )

    assert (
        constraint.ondelete
        == "RESTRICT"
    )


def test_supplier_source_original_unique_index():
    indexes = {
        index.name: index
        for index
        in JournalEntry.__table__.indexes
    }

    index = indexes[
        (
            "uq_journal_entry_original_"
            "supplier_advance_clearing_event"
        )
    ]

    assert index.unique is True

    assert tuple(
        column.name
        for column
        in index.columns
    ) == (
        SOURCE,
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
        "supplier_advance_clearing_event_id "
        "IS NOT NULL"
        in where
    )


def test_supplier_source_participates_in_exclusivity_check():
    checks = {
        constraint.name: constraint
        for constraint
        in JournalEntry.__table__.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    sql = str(
        checks[
            (
                "ck_journal_entries_"
                "at_most_one_business_source"
            )
        ].sqltext
    )

    for source in OTHER_SOURCES:
        assert source in sql

    assert SOURCE in sql
