from datetime import date
from decimal import Decimal

import app.models as models
from app.models.vat_advance_bridge_event import (
    VatAdvanceBridgeEvent,
)


def test_model_is_registered():
    assert (
        models.VatAdvanceBridgeEvent
        is VatAdvanceBridgeEvent
    )


def test_table_name():
    assert (
        VatAdvanceBridgeEvent.__tablename__
        == "vat_advance_bridge_events"
    )


def test_required_columns():
    table = VatAdvanceBridgeEvent.__table__

    assert set(table.c.keys()) == {
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

    assert table.c.id.primary_key is True

    assert table.c.company_id.nullable is False
    assert (
        table.c.tax_calculation_id.nullable
        is False
    )
    assert (
        table.c
        .invoice_fulfillment_allocation_id
        .nullable
        is False
    )
    assert table.c.bridge_date.nullable is False
    assert (
        table.c.bridged_tax_amount.nullable
        is False
    )
    assert table.c.currency_code.nullable is False
    assert table.c.created_by.nullable is False
    assert table.c.created_at.nullable is False
    assert (
        table.c.reversal_of_id.nullable
        is True
    )


def test_named_constraints_exist():
    table = VatAdvanceBridgeEvent.__table__

    names = {
        constraint.name
        for constraint in table.constraints
        if constraint.name is not None
    }

    assert (
        "uq_vat_advance_bridge_events_"
        "company_id_id"
        in names
    )
    assert (
        "uq_vat_advance_bridge_events_"
        "company_id_id_source"
        in names
    )
    assert (
        "uq_vat_advance_bridge_events_"
        "reversal_of"
        in names
    )
    assert (
        "ck_vat_advance_bridge_events_"
        "tax_amount_positive"
        in names
    )
    assert (
        "ck_vat_advance_bridge_events_"
        "currency_code_length"
        in names
    )
    assert (
        "ck_vat_advance_bridge_events_"
        "not_self_reversal"
        in names
    )


def test_named_foreign_keys_exist():
    table = VatAdvanceBridgeEvent.__table__

    foreign_keys = {
        constraint.name: constraint
        for constraint
        in table.foreign_key_constraints
    }

    assert (
        "fk_vat_advance_bridge_events_"
        "company_tax_calculation"
        in foreign_keys
    )
    assert (
        "fk_vat_advance_bridge_events_"
        "company_fulfillment_source"
        in foreign_keys
    )
    assert (
        "fk_vat_advance_bridge_events_"
        "company_reversal_of_source"
        in foreign_keys
    )


def test_reversal_foreign_key_preserves_source_identity():
    table = VatAdvanceBridgeEvent.__table__

    constraint = next(
        constraint
        for constraint
        in table.foreign_key_constraints
        if constraint.name
        == (
            "fk_vat_advance_bridge_events_"
            "company_reversal_of_source"
        )
    )

    assert list(
        constraint.column_keys
    ) == [
        "company_id",
        "reversal_of_id",
        "tax_calculation_id",
        "invoice_fulfillment_allocation_id",
    ]

    assert [
        element.target_fullname
        for element
        in constraint.elements
    ] == [
        "vat_advance_bridge_events.company_id",
        "vat_advance_bridge_events.id",
        (
            "vat_advance_bridge_events."
            "tax_calculation_id"
        ),
        (
            "vat_advance_bridge_events."
            "invoice_fulfillment_allocation_id"
        ),
    ]


def test_source_original_index_is_nonunique():
    table = VatAdvanceBridgeEvent.__table__

    index = next(
        index
        for index in table.indexes
        if index.name
        == (
            "ix_vat_advance_bridge_events_"
            "source_original"
        )
    )

    assert index.unique is False

    assert [
        column.name
        for column
        in index.columns
    ] == [
        "company_id",
        "tax_calculation_id",
        "invoice_fulfillment_allocation_id",
        "id",
    ]

    assert (
        str(
            index.dialect_options[
                "postgresql"
            ][
                "where"
            ]
        )
        == "reversal_of_id IS NULL"
    )


def test_original_event_snapshot():
    event = VatAdvanceBridgeEvent(
        id=1,
        company_id=2,
        tax_calculation_id=3,
        invoice_fulfillment_allocation_id=4,
        bridge_date=date(
            2026,
            9,
            2,
        ),
        bridged_tax_amount=Decimal(
            "20.00"
        ),
        currency_code="UAH",
        created_by=5,
        reversal_of_id=None,
    )

    assert event.id == 1
    assert event.company_id == 2
    assert event.tax_calculation_id == 3
    assert (
        event.invoice_fulfillment_allocation_id
        == 4
    )
    assert (
        event.bridged_tax_amount
        == Decimal("20.00")
    )
    assert event.currency_code == "UAH"
    assert event.reversal_of_id is None


def test_reversal_event_keeps_same_source():
    event = VatAdvanceBridgeEvent(
        id=2,
        company_id=2,
        tax_calculation_id=3,
        invoice_fulfillment_allocation_id=4,
        bridge_date=date(
            2026,
            9,
            3,
        ),
        bridged_tax_amount=Decimal(
            "20.00"
        ),
        currency_code="UAH",
        created_by=5,
        reversal_of_id=1,
    )

    assert event.reversal_of_id == 1
    assert event.company_id == 2
    assert event.tax_calculation_id == 3
    assert (
        event.invoice_fulfillment_allocation_id
        == 4
    )
