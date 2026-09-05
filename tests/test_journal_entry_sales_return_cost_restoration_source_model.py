from itertools import combinations

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
)

from app.models.journal_entry import (
    JournalEntry,
)


SOURCE = (
    "sales_return_cost_restoration_event_id"
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
    SOURCE,
    "purchase_return_recognition_event_id",
    "purchase_return_vat_adjustment_event_id",
    "purchase_return_input_vat_credit_correction_event_id",
)


def test_source_column_exists_and_is_nullable():
    column = (
        JournalEntry
        .__table__
        .c[
            SOURCE
        ]
    )

    assert column.nullable is True


def test_source_has_composite_company_fk():
    constraint = next(
        value
        for value
        in JournalEntry.__table__.constraints
        if (
            isinstance(
                value,
                ForeignKeyConstraint,
            )
            and value.name
            == (
                "fk_journal_entries_company_"
                "sales_return_cost_restoration_event"
            )
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
        (
            "sales_return_cost_restoration_events."
            "company_id"
        ),
        (
            "sales_return_cost_restoration_events."
            "id"
        ),
    )

    assert constraint.ondelete == "RESTRICT"


def test_source_has_nonunique_lookup_index():
    index = next(
        value
        for value
        in JournalEntry.__table__.indexes
        if value.name
        == (
            "ix_journal_entries_"
            "sales_return_cost_restoration_event_id"
        )
    )

    assert index.unique is False

    assert tuple(
        column.name
        for column
        in index.columns
    ) == (
        SOURCE,
    )


def test_original_source_has_partial_unique_index():
    index = next(
        value
        for value
        in JournalEntry.__table__.indexes
        if value.name
        == (
            "uq_journal_entry_original_"
            "sales_return_cost_restoration_event"
        )
    )

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
        "sales_return_cost_restoration_event_id "
        "IS NOT NULL"
        in where
    )


def test_current_business_source_contract_is_14_sources():
    assert len(
        BUSINESS_SOURCES
    ) == 14


def test_current_business_source_contract_has_91_pairs():
    pairs = tuple(
        combinations(
            BUSINESS_SOURCES,
            2,
        )
    )

    assert len(
        pairs
    ) == 91


def test_exclusivity_check_contains_all_91_pairs():
    import re

    constraint = next(
        value
        for value
        in JournalEntry.__table__.constraints
        if (
            isinstance(
                value,
                CheckConstraint,
            )
            and value.name
            == (
                "ck_journal_entries_"
                "at_most_one_business_source"
            )
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

def test_new_source_is_exclusive_with_sales_return_recognition():
    import re

    constraint = next(
        value
        for value
        in JournalEntry.__table__.constraints
        if (
            isinstance(
                value,
                CheckConstraint,
            )
            and value.name
            == (
                "ck_journal_entries_"
                "at_most_one_business_source"
            )
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

    assert (
        "sales_return_recognition_event_id",
        "sales_return_cost_restoration_event_id",
    ) in pairs
