from datetime import date
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
)

from app.models.tax_calculation import (
    TaxCalculation,
)
from app.services.tax_recognition_types import (
    TaxRecognitionMethod,
)
from app.services.tax_treatment_types import (
    TaxTreatment,
)
from app.services.tax_types import (
    TaxDirection,
    TaxType,
)


def test_tax_calculation_table_name():
    assert (
        TaxCalculation.__tablename__
        == "tax_calculations"
    )


def test_tax_calculation_has_expected_columns():
    columns = set(
        TaxCalculation.__table__.columns.keys()
    )

    assert columns == {
        "id",
        "company_id",
        "trade_document_id",
        "trade_document_line_id",
        "product_id",
        "tax_type",
        "direction",
        "tax_rate_code",
        "tax_rate",
        "treatment",
        "recognition_method",
        "taxable_base",
        "tax_amount",
        "currency_code",
        "calculation_date",
        "created_at",
    }


def test_tax_calculation_has_no_mutable_recognition_balance():
    columns = set(
        TaxCalculation.__table__.columns.keys()
    )

    forbidden = {
        "recognized_amount",
        "recognized_tax_amount",
        "recognized_taxable_base",
        "remaining_amount",
        "remaining_tax_amount",
        "remaining_taxable_base",
        "status",
    }

    assert not (
        columns
        & forbidden
    )


def test_tax_calculation_company_identity_constraint():
    constraints = {
        constraint.name
        for constraint
        in TaxCalculation.__table__.constraints
        if isinstance(
            constraint,
            UniqueConstraint,
        )
    }

    assert (
        "uq_tax_calculations_company_id_id"
        in constraints
    )


def test_tax_calculation_source_tax_type_unique_constraint():
    constraint = next(
        constraint
        for constraint
        in TaxCalculation.__table__.constraints
        if (
            isinstance(
                constraint,
                UniqueConstraint,
            )
            and constraint.name
            == (
                "uq_tax_calculations_"
                "source_line_tax_type"
            )
        )
    )

    assert tuple(
        column.name
        for column
        in constraint.columns
    ) == (
        "company_id",
        "trade_document_id",
        "trade_document_line_id",
        "tax_type",
    )


def test_tax_calculation_source_line_composite_fk():
    constraint = next(
        constraint
        for constraint
        in TaxCalculation.__table__.constraints
        if (
            isinstance(
                constraint,
                ForeignKeyConstraint,
            )
            and constraint.name
            == (
                "fk_tax_calculations_"
                "company_trade_document_line_product"
            )
        )
    )

    assert tuple(
        column.name
        for column
        in constraint.columns
    ) == (
        "company_id",
        "trade_document_id",
        "trade_document_line_id",
        "product_id",
    )

    targets = tuple(
        element.target_fullname
        for element
        in constraint.elements
    )

    assert targets == (
        "trade_document_lines.company_id",
        "trade_document_lines.trade_document_id",
        "trade_document_lines.id",
        "trade_document_lines.product_id",
    )

    assert constraint.ondelete == "RESTRICT"


def test_tax_calculation_check_constraints_present():
    names = {
        constraint.name
        for constraint
        in TaxCalculation.__table__.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    assert {
        "ck_tax_calculations_tax_type",
        "ck_tax_calculations_direction",
        "ck_tax_calculations_treatment",
        (
            "ck_tax_calculations_"
            "recognition_method"
        ),
        (
            "ck_tax_calculations_"
            "tax_rate_range"
        ),
        (
            "ck_tax_calculations_"
            "taxable_base_nonnegative"
        ),
        (
            "ck_tax_calculations_"
            "tax_amount_nonnegative"
        ),
        (
            "ck_tax_calculations_"
            "currency_code_length"
        ),
        (
            "ck_tax_calculations_"
            "tax_rate_code_nonempty"
        ),
        (
            "ck_tax_calculations_"
            "treatment_rate_consistency"
        ),
        (
            "ck_tax_calculations_"
            "zero_tax_treatment_amount"
        ),
    }.issubset(
        names
    )


def test_tax_calculation_enum_types_are_explicit():
    table = TaxCalculation.__table__

    assert (
        table.c.tax_type.type.enum_class
        is TaxType
    )

    assert (
        table.c.direction.type.enum_class
        is TaxDirection
    )

    assert (
        table.c.treatment.type.enum_class
        is TaxTreatment
    )

    assert (
        table.c.recognition_method.type.enum_class
        is TaxRecognitionMethod
    )


def test_tax_calculation_snapshot_can_be_constructed():
    calculation = TaxCalculation(
        company_id=1,
        trade_document_id=10,
        trade_document_line_id=20,
        product_id=30,
        tax_type=TaxType.VAT,
        direction=TaxDirection.OUTPUT,
        tax_rate_code="VAT20",
        tax_rate=Decimal("0.20"),
        treatment=TaxTreatment.TAXABLE,
        recognition_method=(
            TaxRecognitionMethod.CASH_METHOD
        ),
        taxable_base=Decimal("100.00"),
        tax_amount=Decimal("20.00"),
        currency_code="UAH",
        calculation_date=date(
            2026,
            8,
            29,
        ),
    )

    assert calculation.tax_type == TaxType.VAT
    assert (
        calculation.direction
        == TaxDirection.OUTPUT
    )
    assert (
        calculation.recognition_method
        == TaxRecognitionMethod.CASH_METHOD
    )
    assert (
        calculation.taxable_base
        == Decimal("100.00")
    )
    assert (
        calculation.tax_amount
        == Decimal("20.00")
    )
