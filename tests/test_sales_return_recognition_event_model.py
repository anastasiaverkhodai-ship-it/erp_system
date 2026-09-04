from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
)

from app.models.sales_return_recognition_event import (
    SalesReturnRecognitionEvent,
)


def test_columns():
    assert tuple(
        column.name
        for column
        in SalesReturnRecognitionEvent
        .__table__.columns
    ) == (
        "id",
        "company_id",
        "trade_return_event_id",
        "sales_recognition_event_id",
        "recognition_date",
        "returned_quantity",
        "returned_gross_amount",
        "returned_tax_amount",
        "currency_code",
        "created_by",
        "created_at",
        "reversal_of_id",
    )


def test_named_source_foreign_keys():
    names = {
        constraint.name
        for constraint
        in SalesReturnRecognitionEvent
        .__table__.constraints
        if isinstance(
            constraint,
            ForeignKeyConstraint,
        )
        and constraint.name is not None
    }

    assert {
        "fk_srre_event_company_trade_return",
        (
            "fk_srre_event_"
            "company_sales_recognition"
        ),
        "fk_srre_event_reversal_source",
    }.issubset(
        names
    )


def test_business_checks():
    names = {
        constraint.name
        for constraint
        in SalesReturnRecognitionEvent
        .__table__.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    assert {
        (
            "ck_srre_event_"
            "returned_quantity_positive"
        ),
        (
            "ck_srre_event_"
            "returned_gross_positive"
        ),
        (
            "ck_srre_event_"
            "returned_tax_nonnegative"
        ),
        (
            "ck_srre_event_"
            "tax_not_above_gross"
        ),
        (
            "ck_srre_event_"
            "currency_length"
        ),
        (
            "ck_srre_event_"
            "not_self_reversal"
        ),
    }.issubset(
        names
    )


def test_reversal_is_unique():
    assert any(
        isinstance(
            constraint,
            UniqueConstraint,
        )
        and constraint.name
        == "uq_srre_event_reversal_of"
        for constraint
        in SalesReturnRecognitionEvent
        .__table__.constraints
    )


def test_pair_history_index_allows_immutable_replacement():
    index = next(
        index
        for index
        in SalesReturnRecognitionEvent
        .__table__.indexes
        if index.name
        == "ix_srre_event_pair_history"
    )

    assert index.unique is False

    assert tuple(
        column.name
        for column in index.columns
    ) == (
        "company_id",
        "trade_return_event_id",
        "sales_recognition_event_id",
        "id",
    )

    assert (
        index.dialect_options[
            "postgresql"
        ].get(
            "where"
        )
        is None
    )
