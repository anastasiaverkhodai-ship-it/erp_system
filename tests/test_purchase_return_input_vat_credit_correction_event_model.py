from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
)

from app.models.purchase_return_input_vat_credit_correction_event import (
    PurchaseReturnInputVatCreditCorrectionEvent,
)


def _constraint(
    kind,
    name,
):
    return next(
        item
        for item
        in (
            PurchaseReturnInputVatCreditCorrectionEvent
            .__table__
            .constraints
        )
        if (
            isinstance(
                item,
                kind,
            )
            and item.name == name
        )
    )


def test_table_name():
    assert (
        PurchaseReturnInputVatCreditCorrectionEvent
        .__tablename__
        == (
            "purchase_return_input_vat_credit_"
            "correction_events"
        )
    )


def test_column_contract():
    table = (
        PurchaseReturnInputVatCreditCorrectionEvent
        .__table__
    )

    expected = {
        "id",
        "company_id",
        "purchase_return_vat_adjustment_event_id",
        "tax_calculation_id",
        "adjustment_date",
        "reduced_taxable_base",
        "reduced_tax_amount",
        "currency_code",
        "created_by",
        "created_at",
        "reversal_of_id",
    }

    assert set(
        table.columns.keys()
    ) == expected

    assert (
        table.c.purchase_return_vat_adjustment_event_id
        .nullable
        is False
    )

    assert (
        table.c.tax_calculation_id.nullable
        is False
    )

    assert (
        table.c.adjustment_date.nullable
        is False
    )

    assert (
        table.c.reduced_taxable_base.nullable
        is False
    )

    assert (
        table.c.reduced_tax_amount.nullable
        is False
    )

    assert (
        table.c.reversal_of_id.nullable
        is True
    )


def test_company_fk():
    constraint = _constraint(
        ForeignKeyConstraint,
        "fk_privcc_event_company",
    )

    assert tuple(
        column.name
        for column in constraint.columns
    ) == (
        "company_id",
    )

    assert tuple(
        element.target_fullname
        for element in constraint.elements
    ) == (
        "companies.id",
    )

    assert constraint.ondelete == "RESTRICT"


def test_created_by_fk():
    constraint = _constraint(
        ForeignKeyConstraint,
        "fk_privcc_event_created_by",
    )

    assert tuple(
        column.name
        for column in constraint.columns
    ) == (
        "created_by",
    )

    assert tuple(
        element.target_fullname
        for element in constraint.elements
    ) == (
        "users.id",
    )

    assert constraint.ondelete == "RESTRICT"


def test_source_composite_fk():
    constraint = _constraint(
        ForeignKeyConstraint,
        (
            "fk_privcc_event_company_"
            "purchase_return_vat_adjustment"
        ),
    )

    assert tuple(
        column.name
        for column in constraint.columns
    ) == (
        "company_id",
        "purchase_return_vat_adjustment_event_id",
    )

    assert tuple(
        element.target_fullname
        for element in constraint.elements
    ) == (
        "purchase_return_vat_adjustment_events.company_id",
        "purchase_return_vat_adjustment_events.id",
    )

    assert constraint.ondelete == "RESTRICT"


def test_tax_calculation_composite_fk():
    constraint = _constraint(
        ForeignKeyConstraint,
        (
            "fk_privcc_event_company_"
            "tax_calculation"
        ),
    )

    assert tuple(
        column.name
        for column in constraint.columns
    ) == (
        "company_id",
        "tax_calculation_id",
    )

    assert tuple(
        element.target_fullname
        for element in constraint.elements
    ) == (
        "tax_calculations.company_id",
        "tax_calculations.id",
    )


def test_reversal_preserves_source_identity():
    constraint = _constraint(
        ForeignKeyConstraint,
        (
            "fk_privcc_event_"
            "reversal_source"
        ),
    )

    assert tuple(
        column.name
        for column in constraint.columns
    ) == (
        "company_id",
        "reversal_of_id",
        "purchase_return_vat_adjustment_event_id",
        "tax_calculation_id",
    )


def test_one_reversal_per_original():
    constraint = _constraint(
        UniqueConstraint,
        (
            "uq_privcc_event_"
            "reversal_of"
        ),
    )

    assert tuple(
        column.name
        for column in constraint.columns
    ) == (
        "reversal_of_id",
    )


def test_nonnegative_and_nonzero_contract():
    table = (
        PurchaseReturnInputVatCreditCorrectionEvent
        .__table__
    )

    checks = {
        item.name:
        " ".join(
            str(
                item.sqltext
            ).split()
        )
        for item in table.constraints
        if isinstance(
            item,
            CheckConstraint,
        )
    }

    assert (
        checks[
            "ck_privcc_event_taxable_base_nonnegative"
        ]
        == "reduced_taxable_base >= 0"
    )

    assert (
        checks[
            "ck_privcc_event_tax_nonnegative"
        ]
        == "reduced_tax_amount >= 0"
    )

    assert (
        checks[
            "ck_privcc_event_nonzero_correction"
        ]
        == (
            "reduced_taxable_base > 0 "
            "OR reduced_tax_amount > 0"
        )
    )


def test_indexes():
    indexes = {
        index.name:
        tuple(
            column.name
            for column in index.columns
        )
        for index
        in (
            PurchaseReturnInputVatCreditCorrectionEvent
            .__table__
            .indexes
        )
    }

    assert indexes[
        "ix_privcc_event_source_history"
    ] == (
        "company_id",
        "purchase_return_vat_adjustment_event_id",
        "tax_calculation_id",
        "id",
    )

    assert indexes[
        "ix_privcc_event_tax_calculation"
    ] == (
        "company_id",
        "tax_calculation_id",
        "adjustment_date",
        "id",
    )

    assert indexes[
        "ix_privcc_event_adjustment_date"
    ] == (
        "company_id",
        "adjustment_date",
        "id",
    )
