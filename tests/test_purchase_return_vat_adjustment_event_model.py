from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
)

from app.models.purchase_return_vat_adjustment_event import (
    PurchaseReturnVatAdjustmentEvent,
)


def test_columns():
    assert tuple(
        column.name
        for column
        in PurchaseReturnVatAdjustmentEvent.__table__.columns
    ) == (
        "id",
        "company_id",
        "purchase_return_recognition_event_id",
        "tax_calculation_id",
        "adjustment_date",
        "basis_kind",
        "adjusted_taxable_base",
        "adjusted_tax_amount",
        "currency_code",
        "created_by",
        "created_at",
        "reversal_of_id",
    )


def test_table_name():
    assert (
        PurchaseReturnVatAdjustmentEvent.__tablename__
        == "purchase_return_vat_adjustment_events"
    )


def test_named_source_foreign_keys():
    names = {
        constraint.name
        for constraint
        in PurchaseReturnVatAdjustmentEvent.__table__.constraints
        if (
            isinstance(
                constraint,
                ForeignKeyConstraint,
            )
            and constraint.name is not None
        )
    }

    assert {
        (
            "fk_prvae_event_company_"
            "purchase_return_recognition"
        ),
        "fk_prvae_event_company_tax_calculation",
        "fk_prvae_event_reversal_source",
    }.issubset(names)


def test_reversal_fk_preserves_exact_source():
    table = PurchaseReturnVatAdjustmentEvent.__table__

    constraint = next(
        constraint
        for constraint in table.constraints
        if (
            isinstance(
                constraint,
                ForeignKeyConstraint,
            )
            and constraint.name
            == "fk_prvae_event_reversal_source"
        )
    )

    assert tuple(
        column.name
        for column in constraint.columns
    ) == (
        "company_id",
        "reversal_of_id",
        "purchase_return_recognition_event_id",
        "tax_calculation_id",
        "basis_kind",
    )

    assert constraint.ondelete == "RESTRICT"


def test_business_checks():
    names = {
        constraint.name
        for constraint
        in PurchaseReturnVatAdjustmentEvent.__table__.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    assert {
        "ck_prvae_event_basis_kind",
        "ck_prvae_event_taxable_base_nonnegative",
        "ck_prvae_event_tax_nonnegative",
        "ck_prvae_event_nonzero_adjustment",
        "ck_prvae_event_currency_length",
        "ck_prvae_event_not_self_reversal",
    }.issubset(names)


def test_basis_kind_is_fail_closed():
    constraint = next(
        constraint
        for constraint
        in PurchaseReturnVatAdjustmentEvent.__table__.constraints
        if (
            isinstance(
                constraint,
                CheckConstraint,
            )
            and constraint.name
            == "ck_prvae_event_basis_kind"
        )
    )

    sql = " ".join(
        str(constraint.sqltext).split()
    )

    assert "'goods_received_by_supplier'" in sql
    assert "'refund_by_supplier'" in sql


def test_tax_amounts_are_independent_nonnegative_snapshots():
    checks = {
        constraint.name: " ".join(
            str(constraint.sqltext).split()
        )
        for constraint
        in PurchaseReturnVatAdjustmentEvent.__table__.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    assert (
        checks[
            "ck_prvae_event_taxable_base_nonnegative"
        ]
        == "adjusted_taxable_base >= 0"
    )

    assert (
        checks[
            "ck_prvae_event_tax_nonnegative"
        ]
        == "adjusted_tax_amount >= 0"
    )


def test_reversal_is_unique():
    assert any(
        isinstance(
            constraint,
            UniqueConstraint,
        )
        and constraint.name
        == "uq_prvae_event_reversal_of"
        for constraint
        in PurchaseReturnVatAdjustmentEvent.__table__.constraints
    )


def test_source_history_index_allows_replacements():
    index = next(
        index
        for index
        in PurchaseReturnVatAdjustmentEvent.__table__.indexes
        if index.name
        == "ix_prvae_event_source_history"
    )

    assert index.unique is False

    assert tuple(
        column.name
        for column in index.columns
    ) == (
        "company_id",
        "purchase_return_recognition_event_id",
        "tax_calculation_id",
        "id",
    )


def test_no_journal_source_is_added_by_domain_model():
    assert (
        "journal_entries"
        not in {
            foreign_key.column.table.name
            for foreign_key
            in PurchaseReturnVatAdjustmentEvent.__table__.foreign_keys
        }
    )
