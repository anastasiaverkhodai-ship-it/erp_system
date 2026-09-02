from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
)

from app.models.journal_entry import (
    JournalEntry,
)


def _indexes():
    return {
        index.name: index
        for index in JournalEntry.__table__.indexes
    }


def _constraints(
    constraint_type,
):
    return {
        constraint.name: constraint
        for constraint
        in JournalEntry.__table__.constraints
        if isinstance(
            constraint,
            constraint_type,
        )
    }


def test_bridge_typed_source_column_exists_and_is_nullable():
    column = (
        JournalEntry.__table__.c
        .vat_advance_bridge_event_id
    )

    assert column.nullable is True


def test_bridge_typed_source_has_ordinary_index():
    indexes = _indexes()

    name = (
        "ix_journal_entries_"
        "vat_advance_bridge_event_id"
    )

    assert name in indexes

    index = indexes[name]

    assert index.unique is False

    assert [
        column.name
        for column in index.columns
    ] == [
        "vat_advance_bridge_event_id"
    ]


def test_bridge_typed_source_has_same_company_fk():
    constraints = _constraints(
        ForeignKeyConstraint
    )

    name = (
        "fk_journal_entries_"
        "company_vat_advance_bridge_event"
    )

    assert name in constraints

    constraint = constraints[name]

    assert [
        column.name
        for column
        in constraint.columns
    ] == [
        "company_id",
        "vat_advance_bridge_event_id",
    ]

    assert [
        element.target_fullname
        for element
        in constraint.elements
    ] == [
        "vat_advance_bridge_events.company_id",
        "vat_advance_bridge_events.id",
    ]

    assert (
        constraint.ondelete
        == "RESTRICT"
    )


def test_business_source_check_includes_bridge_source():
    constraints = _constraints(
        CheckConstraint
    )

    name = (
        "ck_journal_entries_"
        "at_most_one_business_source"
    )

    assert name in constraints

    sql = " ".join(
        str(
            constraints[name].sqltext
        ).split()
    )

    pairs = (
        (
            "document_id IS NULL "
            "OR vat_advance_bridge_event_id IS NULL"
        ),
        (
            "payment_id IS NULL "
            "OR vat_advance_bridge_event_id IS NULL"
        ),
        (
            "payment_settlement_allocation_id IS NULL "
            "OR vat_advance_bridge_event_id IS NULL"
        ),
        (
            "tax_recognition_event_id IS NULL "
            "OR vat_advance_bridge_event_id IS NULL"
        ),
        (
            "sales_recognition_event_id IS NULL "
            "OR vat_advance_bridge_event_id IS NULL"
        ),
    )

    for pair in pairs:
        assert pair in sql


def test_bridge_original_source_has_partial_unique_index():
    indexes = _indexes()

    name = (
        "uq_journal_entry_original_"
        "vat_advance_bridge_event"
    )

    assert name in indexes

    index: Index = indexes[name]

    assert index.unique is True

    assert [
        column.name
        for column in index.columns
    ] == [
        "vat_advance_bridge_event_id"
    ]

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
        "vat_advance_bridge_event_id IS NOT NULL"
        in where
    )
