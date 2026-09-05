from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
)

from app.models.journal_entry import JournalEntry


SOURCE = (
    "input_vat_fulfillment_bridge_event_id"
)

PARTIAL_INDEX = (
    "uq_journal_entry_original_"
    "input_vat_fulfillment_bridge_event"
)

FK_NAME = (
    "fk_journal_entries_company_"
    "input_vat_fulfillment_bridge_event"
)

CHECK_NAME = (
    "ck_journal_entries_"
    "at_most_one_business_source"
)


def test_input_vat_bridge_source_column():
    table = JournalEntry.__table__

    assert SOURCE in table.c

    column = table.c[SOURCE]

    assert column.nullable is True
    assert column.index is True


def test_input_vat_bridge_composite_company_fk():
    table = JournalEntry.__table__

    matches = [
        constraint
        for constraint
        in table.constraints
        if (
            isinstance(
                constraint,
                ForeignKeyConstraint,
            )
            and constraint.name
            == FK_NAME
        )
    ]

    assert len(
        matches
    ) == 1

    constraint = matches[0]

    assert tuple(
        column.name
        for column
        in constraint.columns
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
            "input_vat_fulfillment_bridge_events"
            ".company_id"
        ),
        (
            "input_vat_fulfillment_bridge_events"
            ".id"
        ),
    )


def test_input_vat_bridge_original_journal_is_unique():
    table = JournalEntry.__table__

    indexes = {
        index.name: index
        for index in table.indexes
    }

    assert PARTIAL_INDEX in indexes

    index = indexes[
        PARTIAL_INDEX
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
        ].get(
            "where"
        )
    )

    assert (
        "reversal_of_id IS NULL"
        in where
    )

    assert (
        SOURCE
        in where
    )


def test_input_vat_source_is_exclusive_with_all_other_business_sources():
    table = JournalEntry.__table__

    matches = [
        constraint
        for constraint
        in table.constraints
        if (
            isinstance(
                constraint,
                CheckConstraint,
            )
            and constraint.name
            == CHECK_NAME
        )
    ]

    assert len(
        matches
    ) == 1

    sql = str(
        matches[0].sqltext
    )

    assert sql.count(
        SOURCE
    ) == 13

    for other_source in (
        "document_id",
        "payment_id",
        "payment_settlement_allocation_id",
        "tax_recognition_event_id",
        "sales_recognition_event_id",
        "vat_advance_bridge_event_id",
    ):
        assert (
            other_source
            in sql
        )
