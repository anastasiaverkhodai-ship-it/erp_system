from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
)

from app.models.tax_recognition_event import (
    TaxRecognitionEvent,
)


def test_tax_credit_evidence_source_column():
    table = TaxRecognitionEvent.__table__

    column = table.columns[
        "tax_credit_evidence_id"
    ]

    assert column.nullable is True


def test_tax_credit_evidence_source_composite_fk():
    table = TaxRecognitionEvent.__table__

    constraints = [
        item
        for item in table.constraints
        if isinstance(
            item,
            ForeignKeyConstraint,
        )
        and item.name
        == (
            "fk_tax_recognition_events_"
            "company_tax_credit_evidence"
        )
    ]

    assert len(constraints) == 1

    constraint = constraints[0]

    assert [
        column.name
        for column in constraint.columns
    ] == [
        "company_id",
        "tax_credit_evidence_id",
        "tax_calculation_id",
    ]

    assert [
        element.target_fullname
        for element in constraint.elements
    ] == [
        "tax_credit_evidence.company_id",
        "tax_credit_evidence.id",
        "tax_credit_evidence.tax_calculation_id",
    ]


def test_tax_recognition_has_three_source_exclusivity():
    table = TaxRecognitionEvent.__table__

    constraints = [
        item
        for item in table.constraints
        if isinstance(
            item,
            CheckConstraint,
        )
        and item.name
        == (
            "ck_tax_recognition_events_"
            "at_most_one_recognition_source"
        )
    ]

    assert len(constraints) == 1

    sql = " ".join(
        str(
            constraints[0].sqltext
        ).split()
    )

    assert (
        "invoice_fulfillment_allocation_id IS NULL"
        in sql
    )
    assert (
        "payment_settlement_allocation_id IS NULL"
        in sql
    )
    assert (
        "tax_credit_evidence_id IS NULL"
        in sql
    )

    assert (
        "invoice_fulfillment_allocation_id IS NULL "
        "OR tax_credit_evidence_id IS NULL"
        in sql
    )

    assert (
        "payment_settlement_allocation_id IS NULL "
        "OR tax_credit_evidence_id IS NULL"
        in sql
    )


def test_tax_credit_evidence_source_partial_index():
    table = TaxRecognitionEvent.__table__

    indexes = {
        item.name: item
        for item in table.indexes
        if isinstance(
            item,
            Index,
        )
    }

    name = (
        "ix_tax_recognition_events_"
        "tax_credit_evidence_source"
    )

    assert name in indexes

    index = indexes[name]

    assert index.unique is False

    assert [
        column.name
        for column in index.columns
    ] == [
        "company_id",
        "tax_calculation_id",
        "tax_credit_evidence_id",
    ]

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
        "tax_credit_evidence_id IS NOT NULL"
        in where
    )
