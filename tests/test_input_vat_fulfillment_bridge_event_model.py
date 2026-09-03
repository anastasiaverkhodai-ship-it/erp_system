from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
)

from app.models import (
    InputVatFulfillmentBridgeEvent,
)


def _constraints(
    constraint_type,
):
    return {
        constraint.name: constraint
        for constraint
        in (
            InputVatFulfillmentBridgeEvent
            .__table__
            .constraints
        )
        if isinstance(
            constraint,
            constraint_type,
        )
        and constraint.name
        is not None
    }


def test_input_vat_bridge_event_table_name():
    assert (
        InputVatFulfillmentBridgeEvent
        .__tablename__
        == "input_vat_fulfillment_bridge_events"
    )


def test_input_vat_bridge_event_columns():
    table = (
        InputVatFulfillmentBridgeEvent
        .__table__
    )

    assert set(
        table.c.keys()
    ) == {
        "id",
        "company_id",
        "tax_calculation_id",
        "invoice_fulfillment_allocation_id",
        "bridge_date",
        "bridged_tax_amount",
        "currency_code",
        "created_by",
        "created_at",
        "reversal_of_id",
    }

    assert (
        table.c.bridged_tax_amount
        .type.precision
        == 18
    )

    assert (
        table.c.bridged_tax_amount
        .type.scale
        == 2
    )


def test_input_vat_bridge_event_unique_constraints():
    constraints = _constraints(
        UniqueConstraint
    )

    assert {
        (
            'uq_ivfb_event_company_id_id'
        ),
        (
            'uq_ivfb_event_company_id_id_source'
        ),
        (
            'uq_ivfb_event_reversal_of'
        ),
    }.issubset(
        constraints
    )

    source_constraint = constraints[
        (
            'uq_ivfb_event_company_id_id_source'
        )
    ]

    assert tuple(
        column.name
        for column
        in source_constraint.columns
    ) == (
        "company_id",
        "id",
        "tax_calculation_id",
        "invoice_fulfillment_allocation_id",
    )


def test_input_vat_bridge_event_source_foreign_keys():
    constraints = _constraints(
        ForeignKeyConstraint
    )

    tax_fk = constraints[
        (
            'fk_ivfb_event_company_tax_calc'
        )
    ]

    assert tuple(
        element.parent.name
        for element
        in tax_fk.elements
    ) == (
        "company_id",
        "tax_calculation_id",
    )

    assert tuple(
        element.target_fullname
        for element
        in tax_fk.elements
    ) == (
        "tax_calculations.company_id",
        "tax_calculations.id",
    )

    allocation_fk = constraints[
        (
            'fk_ivfb_event_company_allocation'
        )
    ]

    assert tuple(
        element.parent.name
        for element
        in allocation_fk.elements
    ) == (
        "company_id",
        "invoice_fulfillment_allocation_id",
    )

    assert tuple(
        element.target_fullname
        for element
        in allocation_fk.elements
    ) == (
        (
            "invoice_fulfillment_allocations."
            "company_id"
        ),
        (
            "invoice_fulfillment_allocations."
            "id"
        ),
    )


def test_input_vat_bridge_event_reversal_fk_preserves_source():
    constraints = _constraints(
        ForeignKeyConstraint
    )

    reversal_fk = constraints[
        (
            'fk_ivfb_event_reversal_source'
        )
    ]

    assert tuple(
        element.parent.name
        for element
        in reversal_fk.elements
    ) == (
        "company_id",
        "reversal_of_id",
        "tax_calculation_id",
        "invoice_fulfillment_allocation_id",
    )

    assert tuple(
        element.target_fullname
        for element
        in reversal_fk.elements
    ) == (
        (
            "input_vat_fulfillment_bridge_events."
            "company_id"
        ),
        (
            "input_vat_fulfillment_bridge_events."
            "id"
        ),
        (
            "input_vat_fulfillment_bridge_events."
            "tax_calculation_id"
        ),
        (
            "input_vat_fulfillment_bridge_events."
            "invoice_fulfillment_allocation_id"
        ),
    )


def test_input_vat_bridge_event_checks():
    constraints = _constraints(
        CheckConstraint
    )

    assert {
        (
            'ck_ivfb_event_tax_amount_positive'
        ),
        (
            'ck_ivfb_event_currency_len'
        ),
        (
            'ck_ivfb_event_not_self_reversal'
        ),
    }.issubset(
        constraints
    )


def test_input_vat_bridge_original_source_history_index():
    indexes = {
        index.name: index
        for index
        in (
            InputVatFulfillmentBridgeEvent
            .__table__
            .indexes
        )
        if isinstance(
            index,
            Index,
        )
    }

    index = indexes[
        (
            'ix_ivfb_event_source_original'
        )
    ]

    assert index.unique is False

    assert tuple(
        column.name
        for column
        in index.columns
    ) == (
        "company_id",
        "tax_calculation_id",
        "invoice_fulfillment_allocation_id",
    )

    predicate = str(
        index.dialect_options[
            "postgresql"
        ][
            "where"
        ]
    )

    assert (
        "reversal_of_id IS NULL"
        in predicate
    )

def test_input_vat_bridge_secondary_indexes_have_safe_names():
    indexes = {
        index.name: index
        for index
        in (
            InputVatFulfillmentBridgeEvent
            .__table__
            .indexes
        )
    }

    assert set(
        indexes
    ) == {
        "ix_ivfb_event_company",
        "ix_ivfb_event_tax_calc",
        "ix_ivfb_event_allocation",
        "ix_ivfb_event_bridge_date",
        "ix_ivfb_event_source_original",
    }

    for name in indexes:
        assert len(
            name
        ) <= 63
