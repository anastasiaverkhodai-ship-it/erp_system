from itertools import combinations

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
)

from app.models.journal_entry import (
    JournalEntry,
)


SOURCE = (
    "sales_return_recognition_event_id"
)

FK_NAME = (
    "fk_journal_entries_company_"
    "sales_return_recognition_event"
)

PARTIAL_INDEX = (
    "uq_journal_entry_original_"
    "sales_return_recognition_event"
)

CHECK_NAME = (
    "ck_journal_entries_"
    "at_most_one_business_source"
)

BUSINESS_SOURCES = (
    "document_id",
    "payment_id",
    "payment_settlement_allocation_id",
    "tax_recognition_event_id",
    "sales_recognition_event_id",
    "vat_advance_bridge_event_id",
    "input_vat_fulfillment_bridge_event_id",
    "supplier_advance_clearing_event_id",
    "customer_advance_clearing_event_id",
    SOURCE,
    "sales_return_cost_restoration_event_id",
    "purchase_return_recognition_event_id",
)


def test_sales_return_source_column():
    table = JournalEntry.__table__

    assert SOURCE in table.c

    column = table.c[
        SOURCE
    ]

    assert column.nullable is True
    assert column.index is True


def test_sales_return_source_composite_company_fk():
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
        str(
            element.column
        )
        for element
        in constraint.elements
    ) == (
        "sales_return_recognition_events.company_id",
        "sales_return_recognition_events.id",
    )

    assert (
        constraint.ondelete
        == "RESTRICT"
    )


def test_sales_return_source_original_partial_unique_index():
    table = JournalEntry.__table__

    index = next(
        index
        for index
        in table.indexes
        if index.name
        == PARTIAL_INDEX
    )

    assert index.unique is True

    assert tuple(
        column.name
        for column
        in index.columns
    ) == (
        SOURCE,
    )

    where = " ".join(
        str(
            index.dialect_options[
                "postgresql"
            ]["where"]
        ).split()
    )

    assert (
        "reversal_of_id IS NULL"
        in where
    )

    assert (
        f"{SOURCE} IS NOT NULL"
        in where
    )


def test_business_source_contract_is_12_sources_66_pairs():
    assert len(
        BUSINESS_SOURCES
    ) == 12

    pairs = tuple(
        combinations(
            BUSINESS_SOURCES,
            2,
        )
    )

    assert len(
        pairs
    ) == 66

    table = JournalEntry.__table__

    check = next(
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
    )

    sql = " ".join(
        str(
            check.sqltext
        ).split()
    )

    for left, right in pairs:
        assert (
            f"{left} IS NULL "
            f"OR {right} IS NULL"
            in sql
        )


def test_sales_return_source_has_eleven_exclusion_pairs():
    table = JournalEntry.__table__

    check = next(
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
    )

    sql = str(
        check.sqltext
    )

    assert (
        sql.count(
            SOURCE
        )
        == 11
    )
