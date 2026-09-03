from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Numeric,
    UniqueConstraint,
)

from app.models import (
    SupplierAdvanceClearingEvent,
)


def test_supplier_advance_clearing_event_table():
    table = (
        SupplierAdvanceClearingEvent
        .__table__
    )

    assert table.name == (
        "supplier_advance_clearing_events"
    )

    assert tuple(
        table.c.keys()
    ) == (
        "id",
        "company_id",
        "payment_settlement_allocation_id",
        "invoice_fulfillment_allocation_id",
        "clearing_date",
        "cleared_amount",
        "currency_code",
        "created_by",
        "created_at",
        "reversal_of_id",
    )


def test_supplier_advance_clearing_amount_type():
    column = (
        SupplierAdvanceClearingEvent
        .__table__
        .c
        .cleared_amount
    )

    assert isinstance(
        column.type,
        Numeric,
    )

    assert (
        column.type.precision
        == 18
    )

    assert (
        column.type.scale
        == 2
    )


def test_supplier_advance_clearing_constraints():
    table = (
        SupplierAdvanceClearingEvent
        .__table__
    )

    names = {
        constraint.name
        for constraint
        in table.constraints
        if constraint.name
    }

    required = {
        "uq_sac_event_company_id_id",
        "uq_sac_event_company_id_id_source",
        "uq_sac_event_reversal_of",
        "fk_sac_event_company_settlement",
        "fk_sac_event_company_liability",
        "fk_sac_event_reversal_source",
        "ck_sac_event_amount_positive",
        "ck_sac_event_currency_len",
        "ck_sac_event_not_self_reversal",
    }

    assert required.issubset(
        names
    )


def test_supplier_advance_clearing_composite_fks():
    table = (
        SupplierAdvanceClearingEvent
        .__table__
    )

    foreign_keys = {
        constraint.name: (
            tuple(
                column.name
                for column
                in constraint.columns
            ),
            tuple(
                element.target_fullname
                for element
                in constraint.elements
            ),
        )
        for constraint
        in table.constraints
        if isinstance(
            constraint,
            ForeignKeyConstraint,
        )
    }

    assert foreign_keys[
        "fk_sac_event_company_settlement"
    ][0] == (
        "company_id",
        "payment_settlement_allocation_id",
    )

    assert foreign_keys[
        "fk_sac_event_company_liability"
    ][0] == (
        "company_id",
        "invoice_fulfillment_allocation_id",
    )

    reversal = foreign_keys[
        "fk_sac_event_reversal_source"
    ]

    assert reversal[0] == (
        "company_id",
        "reversal_of_id",
        "payment_settlement_allocation_id",
        "invoice_fulfillment_allocation_id",
    )


def test_supplier_advance_clearing_indexes():
    table = (
        SupplierAdvanceClearingEvent
        .__table__
    )

    indexes = {
        index.name: index
        for index
        in table.indexes
    }

    required = {
        "ix_sac_event_company",
        "ix_sac_event_settlement",
        "ix_sac_event_liability",
        "ix_sac_event_date",
        "ix_sac_event_source_original",
    }

    assert required == set(
        indexes
    )

    source = indexes[
        "ix_sac_event_source_original"
    ]

    assert isinstance(
        source,
        Index,
    )

    assert source.unique is False

    assert tuple(
        column.name
        for column
        in source.columns
    ) == (
        "company_id",
        "payment_settlement_allocation_id",
        "invoice_fulfillment_allocation_id",
        "id",
    )

    assert (
        source.dialect_options[
            "postgresql"
        ][
            "where"
        ]
        is not None
    )


def test_unique_constraint_shapes():
    table = (
        SupplierAdvanceClearingEvent
        .__table__
    )

    uniques = {
        constraint.name: tuple(
            column.name
            for column
            in constraint.columns
        )
        for constraint
        in table.constraints
        if isinstance(
            constraint,
            UniqueConstraint,
        )
    }

    assert uniques[
        "uq_sac_event_company_id_id"
    ] == (
        "company_id",
        "id",
    )

    assert uniques[
        "uq_sac_event_company_id_id_source"
    ] == (
        "company_id",
        "id",
        "payment_settlement_allocation_id",
        "invoice_fulfillment_allocation_id",
    )

    assert uniques[
        "uq_sac_event_reversal_of"
    ] == (
        "reversal_of_id",
    )


def test_check_constraints_are_named():
    table = (
        SupplierAdvanceClearingEvent
        .__table__
    )

    checks = {
        constraint.name
        for constraint
        in table.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    assert checks == {
        "ck_sac_event_amount_positive",
        "ck_sac_event_currency_len",
        "ck_sac_event_not_self_reversal",
    }
