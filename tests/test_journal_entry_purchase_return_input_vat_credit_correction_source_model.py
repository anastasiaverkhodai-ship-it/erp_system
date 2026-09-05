from collections import Counter
import re

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
)

from app.models.journal_entry import (
    JournalEntry,
)


SOURCE = (
    "purchase_return_input_vat_credit_"
    "correction_event_id"
)


def test_legal_correction_source_column():
    table = JournalEntry.__table__

    assert SOURCE in table.c

    assert (
        table.c[
            SOURCE
        ].nullable
        is True
    )


def test_legal_correction_composite_company_fk():
    constraint = next(
        item
        for item
        in JournalEntry.__table__.constraints
        if (
            isinstance(
                item,
                ForeignKeyConstraint,
            )
            and item.name
            == (
                "fk_je_company_pr_input_vat_"
                "credit_correction_event"
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
        element.target_fullname
        for element
        in constraint.elements
    ) == (
        (
            "purchase_return_input_vat_credit_"
            "correction_events.company_id"
        ),
        (
            "purchase_return_input_vat_credit_"
            "correction_events.id"
        ),
    )

    assert (
        constraint.ondelete
        == "RESTRICT"
    )


def test_legal_correction_source_indexes():
    indexes = {
        index.name:
        index
        for index
        in JournalEntry.__table__.indexes
    }

    lookup = indexes[
        "ix_je_pr_input_vat_credit_correction_event_id"
    ]

    assert lookup.unique is False

    assert tuple(
        column.name
        for column
        in lookup.columns
    ) == (
        SOURCE,
    )

    unique = indexes[
        "uq_je_original_pr_input_vat_credit_correction_event"
    ]

    assert unique.unique is True

    assert tuple(
        column.name
        for column
        in unique.columns
    ) == (
        SOURCE,
    )

    where = " ".join(
        str(
            unique.dialect_options[
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


def test_business_source_contract_is_14_91_13():
    constraint = next(
        item
        for item
        in JournalEntry.__table__.constraints
        if (
            isinstance(
                item,
                CheckConstraint,
            )
            and item.name
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

    tokens = tuple(
        token.lower()
        for token
        in re.findall(
            r'([A-Za-z0-9_]+_id)"?\s+IS\s+NULL',
            sql,
            flags=re.IGNORECASE,
        )
    )

    counts = Counter(
        tokens
    )

    assert len(
        counts
    ) == 14

    assert len(
        tokens
    ) == 182

    assert len(
        re.findall(
            r"\bOR\b",
            sql,
            flags=re.IGNORECASE,
        )
    ) == 91

    assert set(
        counts.values()
    ) == {
        13
    }

    assert (
        counts[
            SOURCE
        ]
        == 13
    )
