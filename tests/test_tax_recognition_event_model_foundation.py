from datetime import date
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
)

from app.models.tax_recognition_event import (
    TaxRecognitionEvent,
)


def test_table_name():
    assert (
        TaxRecognitionEvent.__tablename__
        == "tax_recognition_events"
    )


def test_columns():
    assert set(
        TaxRecognitionEvent.__table__.columns.keys()
    ) == {
        "id",
        "company_id",
        "tax_calculation_id",
        "invoice_fulfillment_allocation_id",
        "payment_settlement_allocation_id",
        "recognition_date",
        "recognized_taxable_base",
        "recognized_tax_amount",
        "currency_code",
        "created_by",
        "created_at",
        "reversal_of_id",
    }


def test_no_mutable_recognition_status():
    columns = set(
        TaxRecognitionEvent.__table__.columns.keys()
    )

    assert "status" not in columns
    assert "reversed_at" not in columns
    assert "reversed_by" not in columns
    assert "remaining_tax_amount" not in columns
    assert "remaining_taxable_base" not in columns


def test_identity_unique_constraints():
    constraints = {
        constraint.name
        for constraint
        in TaxRecognitionEvent.__table__.constraints
        if isinstance(
            constraint,
            UniqueConstraint,
        )
    }

    assert (
        "uq_tax_recognition_events_company_id_id"
        in constraints
    )

    assert (
        "uq_tax_recognition_events_"
        "company_id_id_tax_calculation"
        in constraints
    )

    assert (
        "uq_tax_recognition_events_reversal_of"
        in constraints
    )


def _foreign_key(name):
    return next(
        constraint
        for constraint
        in TaxRecognitionEvent.__table__.constraints
        if (
            isinstance(
                constraint,
                ForeignKeyConstraint,
            )
            and constraint.name == name
        )
    )


def test_tax_calculation_company_fk():
    constraint = _foreign_key(
        (
            "fk_tax_recognition_events_"
            "company_tax_calculation"
        )
    )

    assert tuple(
        column.name
        for column
        in constraint.columns
    ) == (
        "company_id",
        "tax_calculation_id",
    )

    assert tuple(
        element.target_fullname
        for element
        in constraint.elements
    ) == (
        "tax_calculations.company_id",
        "tax_calculations.id",
    )

    assert constraint.ondelete == "RESTRICT"


def test_fulfillment_allocation_company_fk():
    constraint = _foreign_key(
        (
            "fk_tax_recognition_events_"
            "company_invoice_fulfillment_alloc"
        )
    )

    assert tuple(
        column.name
        for column
        in constraint.columns
    ) == (
        "company_id",
        "invoice_fulfillment_allocation_id",
    )

    assert tuple(
        element.target_fullname
        for element
        in constraint.elements
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

    assert constraint.ondelete == "RESTRICT"


def test_settlement_allocation_company_fk():
    constraint = _foreign_key(
        (
            "fk_tax_recognition_events_"
            "company_payment_settlement_allocation"
        )
    )

    assert tuple(
        column.name
        for column
        in constraint.columns
    ) == (
        "company_id",
        "payment_settlement_allocation_id",
    )

    assert tuple(
        element.target_fullname
        for element
        in constraint.elements
    ) == (
        (
            "payment_settlement_allocations."
            "company_id"
        ),
        (
            "payment_settlement_allocations."
            "id"
        ),
    )


def test_reversal_must_use_same_tax_calculation():
    constraint = _foreign_key(
        (
            "fk_tax_recognition_events_"
            "company_reversal_of_tax_calculation"
        )
    )

    assert tuple(
        column.name
        for column
        in constraint.columns
    ) == (
        "company_id",
        "reversal_of_id",
        "tax_calculation_id",
    )

    assert tuple(
        element.target_fullname
        for element
        in constraint.elements
    ) == (
        "tax_recognition_events.company_id",
        "tax_recognition_events.id",
        (
            "tax_recognition_events."
            "tax_calculation_id"
        ),
    )


def test_check_constraints():
    names = {
        constraint.name
        for constraint
        in TaxRecognitionEvent.__table__.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    assert {
        (
            "ck_tax_recognition_events_"
            "at_most_one_recognition_source"
        ),
        (
            "ck_tax_recognition_events_"
            "taxable_base_nonnegative"
        ),
        (
            "ck_tax_recognition_events_"
            "tax_amount_nonnegative"
        ),
        (
            "ck_tax_recognition_events_"
            "nonzero_recognition"
        ),
        (
            "ck_tax_recognition_events_"
            "currency_code_length"
        ),
        (
            "ck_tax_recognition_events_"
            "not_self_reversal"
        ),
    }.issubset(names)


def test_fulfillment_source_index():
    index = next(
        index
        for index
        in TaxRecognitionEvent.__table__.indexes
        if index.name
        == (
            "ix_tax_recognition_events_"
            "fulfillment_source"
        )
    )

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

    where = str(
        index.dialect_options[
            "postgresql"
        ]["where"]
    )

    assert "reversal_of_id IS NULL" in where
    assert (
        "invoice_fulfillment_allocation_id "
        "IS NOT NULL"
        in where
    )


def test_settlement_source_index():
    index = next(
        index
        for index
        in TaxRecognitionEvent.__table__.indexes
        if index.name
        == (
            "ix_tax_recognition_events_"
            "settlement_source"
        )
    )

    assert index.unique is False

    assert tuple(
        column.name
        for column
        in index.columns
    ) == (
        "company_id",
        "tax_calculation_id",
        "payment_settlement_allocation_id",
    )

    where = str(
        index.dialect_options[
            "postgresql"
        ]["where"]
    )

    assert "reversal_of_id IS NULL" in where
    assert (
        "payment_settlement_allocation_id "
        "IS NOT NULL"
        in where
    )


def test_can_construct_cash_recognition_event():
    event = TaxRecognitionEvent(
        company_id=1,
        tax_calculation_id=10,
        invoice_fulfillment_allocation_id=None,
        payment_settlement_allocation_id=20,
        recognition_date=date(
            2026,
            8,
            29,
        ),
        recognized_taxable_base=Decimal(
            "50.00"
        ),
        recognized_tax_amount=Decimal(
            "10.00"
        ),
        currency_code="UAH",
        created_by=1,
        reversal_of_id=None,
    )

    assert (
        event.payment_settlement_allocation_id
        == 20
    )
    assert (
        event.invoice_fulfillment_allocation_id
        is None
    )


def test_can_construct_fulfillment_recognition_event():
    event = TaxRecognitionEvent(
        company_id=1,
        tax_calculation_id=10,
        invoice_fulfillment_allocation_id=30,
        payment_settlement_allocation_id=None,
        recognition_date=date(
            2026,
            8,
            29,
        ),
        recognized_taxable_base=Decimal(
            "100.00"
        ),
        recognized_tax_amount=Decimal(
            "20.00"
        ),
        currency_code="UAH",
        created_by=1,
        reversal_of_id=None,
    )

    assert (
        event.invoice_fulfillment_allocation_id
        == 30
    )
