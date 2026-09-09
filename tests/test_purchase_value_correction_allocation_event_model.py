from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
)

from app.models.purchase_value_correction_allocation_event import (
    PurchaseValueCorrectionAllocationEvent,
)


def columns():
    return tuple(
        column.name
        for column
        in PurchaseValueCorrectionAllocationEvent
        .__table__.columns
    )


def named_constraints(
    constraint_type,
):
    return {
        constraint.name
        for constraint
        in PurchaseValueCorrectionAllocationEvent
        .__table__.constraints
        if (
            isinstance(
                constraint,
                constraint_type,
            )
            and constraint.name
        )
    }


def test_table_name():
    assert (
        PurchaseValueCorrectionAllocationEvent
        .__tablename__
        == (
            "purchase_value_correction_"
            "allocation_events"
        )
    )


def test_columns():
    assert columns() == (
        "id",
        "company_id",
        "trade_value_correction_event_id",
        "invoice_fulfillment_allocation_id",
        "recognition_date",
        "original_allocated_base_amount",
        "corrected_allocated_base_amount",
        "currency_code",
        "created_by",
        "created_at",
        "reversal_of_id",
    )


def test_named_foreign_keys():
    assert {
        "fk_pvca_event_company",
        "fk_pvca_event_created_by",
        (
            "fk_pvca_event_"
            "trade_value_correction"
        ),
        (
            "fk_pvca_event_"
            "invoice_fulfillment_allocation"
        ),
        "fk_pvca_event_reversal_source",
    }.issubset(
        named_constraints(
            ForeignKeyConstraint
        )
    )


def test_named_checks():
    assert {
        (
            "ck_pvca_event_"
            "original_base_nonnegative"
        ),
        (
            "ck_pvca_event_"
            "corrected_base_nonnegative"
        ),
        "ck_pvca_event_not_noop",
        "ck_pvca_event_currency_length",
        "ck_pvca_event_not_self_reversal",
    }.issubset(
        named_constraints(
            CheckConstraint
        )
    )


def test_named_uniques():
    assert {
        "uq_pvca_event_company_id_id",
        "uq_pvca_event_company_id_id_source",
        "uq_pvca_event_reversal_of",
    }.issubset(
        named_constraints(
            UniqueConstraint
        )
    )


def test_source_index():
    indexes = {
        index.name: index
        for index
        in PurchaseValueCorrectionAllocationEvent
        .__table__.indexes
        if isinstance(
            index,
            Index,
        )
    }

    assert set(
        indexes
    ) == {
        "ix_pvca_event_source",
    }

    assert tuple(
        column.name
        for column
        in indexes[
            "ix_pvca_event_source"
        ].columns
    ) == (
        "company_id",
        "trade_value_correction_event_id",
        "invoice_fulfillment_allocation_id",
    )


def test_allocated_base_delta_property():
    event = (
        PurchaseValueCorrectionAllocationEvent(
            company_id=1,
            trade_value_correction_event_id=2,
            invoice_fulfillment_allocation_id=3,
            original_allocated_base_amount=(
                Decimal("10.00")
            ),
            corrected_allocated_base_amount=(
                Decimal("8.50")
            ),
            currency_code="UAH",
            created_by=4,
        )
    )

    assert (
        event.allocated_base_delta
        == Decimal("-1.50")
    )


def test_increase_delta_property():
    event = (
        PurchaseValueCorrectionAllocationEvent(
            company_id=1,
            trade_value_correction_event_id=2,
            invoice_fulfillment_allocation_id=3,
            original_allocated_base_amount=(
                Decimal("8.50")
            ),
            corrected_allocated_base_amount=(
                Decimal("10.00")
            ),
            currency_code="UAH",
            created_by=4,
        )
    )

    assert (
        event.allocated_base_delta
        == Decimal("1.50")
    )
