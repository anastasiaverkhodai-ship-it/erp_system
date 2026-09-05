import re
from itertools import combinations

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
)

from app.models.journal_entry import (
    JournalEntry,
)


SOURCE = (
    "purchase_return_recognition_event_id"
)

FK_NAME = (
    "fk_journal_entries_company_"
    "purchase_return_recognition_event"
)

LOOKUP_INDEX = (
    "ix_journal_entries_"
    "purchase_return_recognition_event_id"
)

PARTIAL_INDEX = (
    "uq_journal_entry_original_"
    "purchase_return_recognition_event"
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
    "sales_return_recognition_event_id",
    "sales_return_cost_restoration_event_id",
    SOURCE,
)


def test_purchase_return_source_column():
    column = (
        JournalEntry
        .__table__
        .c[
            SOURCE
        ]
    )

    assert column.nullable is True
    assert column.index is True


def test_purchase_return_source_composite_company_fk():
    table = JournalEntry.__table__

    constraint = next(
        value
        for value
        in table.constraints
        if (
            isinstance(
                value,
                ForeignKeyConstraint,
            )
            and value.name == FK_NAME
        )
    )

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
        "purchase_return_recognition_events.company_id",
        "purchase_return_recognition_events.id",
    )

    assert constraint.ondelete == "RESTRICT"


def test_purchase_return_source_has_nonunique_lookup_index():
    index = next(
        value
        for value
        in JournalEntry.__table__.indexes
        if value.name == LOOKUP_INDEX
    )

    assert index.unique is False

    assert tuple(
        column.name
        for column
        in index.columns
    ) == (
        SOURCE,
    )


def test_purchase_return_source_original_partial_unique_index():
    index = next(
        value
        for value
        in JournalEntry.__table__.indexes
        if value.name == PARTIAL_INDEX
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
            ][
                "where"
            ]
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


def test_exclusivity_check_contains_exact_66_pairs():
    constraint = next(
        value
        for value
        in JournalEntry.__table__.constraints
        if (
            isinstance(
                value,
                CheckConstraint,
            )
            and value.name == CHECK_NAME
        )
    )

    pair_pattern = re.compile(
        r"\(\s*"
        r"([a-z_]+)\s+IS\s+NULL"
        r"\s+OR\s+"
        r"([a-z_]+)\s+IS\s+NULL"
        r"\s*\)"
    )

    actual_pairs = set(
        pair_pattern.findall(
            str(
                constraint.sqltext
            )
        )
    )

    expected_pairs = set(
        combinations(
            BUSINESS_SOURCES,
            2,
        )
    )

    assert actual_pairs == expected_pairs


def test_purchase_return_source_has_11_exclusion_pairs():
    constraint = next(
        value
        for value
        in JournalEntry.__table__.constraints
        if (
            isinstance(
                value,
                CheckConstraint,
            )
            and value.name == CHECK_NAME
        )
    )

    pair_pattern = re.compile(
        r"\(\s*"
        r"([a-z_]+)\s+IS\s+NULL"
        r"\s+OR\s+"
        r"([a-z_]+)\s+IS\s+NULL"
        r"\s*\)"
    )

    pairs = set(
        pair_pattern.findall(
            str(
                constraint.sqltext
            )
        )
    )

    source_pairs = {
        pair
        for pair
        in pairs
        if SOURCE in pair
    }

    assert len(
        source_pairs
    ) == 11
