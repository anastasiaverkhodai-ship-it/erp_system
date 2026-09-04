from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
)

from app.models.trade_return_event import (
    TradeReturnEvent,
)
from app.models.trade_value_correction_event import (
    TradeValueCorrectionEvent,
)


def columns(
    model,
):
    return tuple(
        column.name
        for column
        in model.__table__.columns
    )


def named_constraints(
    model,
):
    return {
        constraint.name
        for constraint
        in model.__table__.constraints
        if constraint.name is not None
    }


def named_fks(
    model,
):
    return {
        constraint.name
        for constraint
        in model.__table__.constraints
        if isinstance(
            constraint,
            ForeignKeyConstraint,
        )
        and constraint.name is not None
    }


def named_checks(
    model,
):
    return {
        constraint.name
        for constraint
        in model.__table__.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }


def test_trade_return_event_columns():
    assert columns(
        TradeReturnEvent
    ) == (
        "id",
        "company_id",
        "direction",
        "original_fulfillment_id",
        "original_trade_document_id",
        "original_trade_document_line_id",
        "original_fulfillment_line_id",
        "product_id",
        "return_document_id",
        "return_document_type",
        "return_document_line_id",
        "return_warehouse_id",
        "return_date",
        "returned_quantity",
        "reason_code",
        "created_by",
        "created_at",
        "reversal_of_id",
    )


def test_trade_return_full_provenance_fks():
    assert {
        "fk_tre_event_original_trade_line",
        (
            "fk_tre_event_"
            "original_fulfillment_line"
        ),
        "fk_tre_event_return_document",
        "fk_tre_event_return_document_line",
        "fk_tre_event_reversal_source",
    }.issubset(
        named_fks(
            TradeReturnEvent
        )
    )


def test_trade_return_business_checks():
    assert {
        "ck_tre_event_direction",
        (
            "ck_tre_event_"
            "return_document_type"
        ),
        (
            "ck_tre_event_"
            "direction_document_type"
        ),
        (
            "ck_tre_event_"
            "returned_quantity_positive"
        ),
        "ck_tre_event_reason_nonempty",
        "ck_tre_event_not_self_reversal",
    }.issubset(
        named_checks(
            TradeReturnEvent
        )
    )


def test_trade_return_reversal_unique():
    assert any(
        isinstance(
            constraint,
            UniqueConstraint,
        )
        and constraint.name
        == "uq_tre_event_reversal_of"
        for constraint
        in TradeReturnEvent
        .__table__.constraints
    )


def test_trade_return_indexes():
    assert {
        index.name
        for index
        in TradeReturnEvent
        .__table__.indexes
    } == {
        "uq_tre_event_original_return_line",
        (
            "ix_tre_event_"
            "original_fulfillment_source"
        ),
    }


def test_value_correction_columns():
    assert columns(
        TradeValueCorrectionEvent
    ) == (
        "id",
        "company_id",
        "direction",
        "trade_document_id",
        "trade_document_line_id",
        "product_id",
        "correction_date",
        "original_gross_amount",
        "original_tax_amount",
        "corrected_gross_amount",
        "corrected_tax_amount",
        "currency_code",
        "reason_code",
        "created_by",
        "created_at",
        "reversal_of_id",
    )


def test_value_correction_source_fks():
    assert {
        (
            "fk_tvce_event_"
            "trade_document_line"
        ),
        (
            "fk_tvce_event_"
            "reversal_source"
        ),
    }.issubset(
        named_fks(
            TradeValueCorrectionEvent
        )
    )


def test_value_correction_business_checks():
    assert {
        "ck_tvce_event_direction",
        (
            "ck_tvce_event_"
            "original_gross_nonnegative"
        ),
        (
            "ck_tvce_event_"
            "original_tax_nonnegative"
        ),
        (
            "ck_tvce_event_"
            "original_tax_not_above_gross"
        ),
        (
            "ck_tvce_event_"
            "corrected_gross_nonnegative"
        ),
        (
            "ck_tvce_event_"
            "corrected_tax_nonnegative"
        ),
        (
            "ck_tvce_event_"
            "corrected_tax_not_above_gross"
        ),
        "ck_tvce_event_not_noop",
        "ck_tvce_event_currency_length",
        "ck_tvce_event_reason_nonempty",
        "ck_tvce_event_not_self_reversal",
    }.issubset(
        named_checks(
            TradeValueCorrectionEvent
        )
    )


def test_value_correction_reversal_unique():
    assert any(
        isinstance(
            constraint,
            UniqueConstraint,
        )
        and constraint.name
        == "uq_tvce_event_reversal_of"
        for constraint
        in TradeValueCorrectionEvent
        .__table__.constraints
    )


def test_models_are_separate_sources():
    assert (
        TradeReturnEvent.__tablename__
        == "trade_return_events"
    )

    assert (
        TradeValueCorrectionEvent.__tablename__
        == "trade_value_correction_events"
    )

    assert (
        TradeReturnEvent.__table__
        is not
        TradeValueCorrectionEvent.__table__
    )


def test_named_constraints_exist():
    assert (
        "uq_tre_event_company_id_id_source"
        in named_constraints(
            TradeReturnEvent
        )
    )

    assert (
        "uq_tvce_event_company_id_id_source"
        in named_constraints(
            TradeValueCorrectionEvent
        )
    )
