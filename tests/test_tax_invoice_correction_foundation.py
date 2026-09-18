from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
)

import app.models  # noqa: F401
from app.core.database import Base


HEADER = "tax_invoice_corrections"
LINES = "tax_invoice_correction_lines"
HISTORY = "tax_invoice_correction_registration_events"


def _constraint_names(table_name):
    table = Base.metadata.tables[
        table_name
    ]

    return {
        item.name
        for item in table.constraints
        if item.name
    }


def _index_names(table_name):
    table = Base.metadata.tables[
        table_name
    ]

    return {
        item.name
        for item in table.indexes
        if item.name
    }


def test_rk_tables_are_registered():
    assert HEADER in Base.metadata.tables
    assert LINES in Base.metadata.tables
    assert HISTORY in Base.metadata.tables


def test_rk_header_columns_are_canonical():
    table = Base.metadata.tables[
        HEADER
    ]

    assert set(
        table.c.keys()
    ) == {
        "id",
        "company_id",
        "original_tax_invoice_id",
        "direction",
        "registration_party",
        "document_number",
        "document_date",
        "currency_code",
        "seller_name",
        "seller_tax_number",
        "seller_vat_number",
        "buyer_name",
        "buyer_tax_number",
        "buyer_vat_number",
        "created_by",
        "created_at",
    }

    assert "status" not in table.c


def test_rk_header_links_original_tax_invoice():
    names = _constraint_names(
        HEADER
    )

    assert "fk_tic_original_invoice" in names
    assert "ck_tic_direction" in names
    assert "ck_tic_uah" in names
    assert "ck_tic_buyer_shape" in names


def test_rk_line_has_original_line_and_exact_source_surface():
    table = Base.metadata.tables[
        LINES
    ]

    required = {
        "original_tax_invoice_line_id",
        "source_kind",
        "sales_return_recognition_event_id",
        "trade_value_correction_event_id",
        "purchase_return_vat_adjustment_event_id",
        "purchase_value_correction_vat_adjustment_event_id",
        "tax_recognition_reversal_event_id",
        "tax_credit_evidence_id",
    }

    assert required <= set(
        table.c.keys()
    )

    names = _constraint_names(
        LINES
    )

    assert "fk_ticl_original_line" in names
    assert "ck_ticl_source_kind" in names
    assert "ck_ticl_source_shape" in names


def test_rk_line_signed_delta_columns_are_not_nonnegative_locked():
    table = Base.metadata.tables[
        LINES
    ]

    delta_names = {
        "quantity_delta",
        "unit_price_without_vat_delta",
        "taxable_base_delta",
        "tax_amount_delta",
        "total_with_vat_delta",
    }

    assert delta_names <= set(
        table.c.keys()
    )

    checks = {
        str(item.sqltext)
        for item in table.constraints
        if isinstance(
            item,
            CheckConstraint,
        )
    }

    joined = "\n".join(
        checks
    ).lower()

    for name in delta_names:
        assert (
            f"{name} >= 0"
            not in joined
        )

    assert "ck_ticl_nonzero_delta" in _constraint_names(
        LINES
    )
    assert "ck_ticl_total_math" in _constraint_names(
        LINES
    )


def test_rk_line_has_composite_tenant_foreign_keys():
    table = Base.metadata.tables[
        LINES
    ]

    fk_names = {
        item.name
        for item in table.constraints
        if isinstance(
            item,
            ForeignKeyConstraint,
        )
        and item.name
    }

    assert {
        "fk_ticl_correction",
        "fk_ticl_original_line",
        "fk_ticl_sales_return",
        "fk_ticl_value_correction",
        "fk_ticl_purchase_return",
        "fk_ticl_purchase_value",
        "fk_ticl_recognition_reversal",
        "fk_ticl_credit_evidence",
    } <= fk_names


def test_rk_line_sources_have_natural_unique_indexes():
    indexes = _index_names(
        LINES
    )

    assert {
        "ux_ticl_sales_return_source",
        "ux_ticl_value_source",
        "ux_ticl_purchase_return_source",
        "ux_ticl_purchase_value_source",
        "ux_ticl_recognition_reversal_source",
    } <= indexes


def test_rk_registration_history_is_append_only_shape():
    table = Base.metadata.tables[
        HISTORY
    ]

    assert set(
        table.c.keys()
    ) == {
        "id",
        "company_id",
        "tax_invoice_correction_id",
        "status",
        "registration_party",
        "received_on",
        "event_date",
        "reference",
        "created_by",
        "created_at",
    }

    assert "current_status" not in table.c

    names = _constraint_names(
        HISTORY
    )

    assert "fk_ticre_correction" in names
    assert "ck_ticre_status" in names
    assert "ck_ticre_reference" in names


def test_rk_header_has_no_economic_source_columns():
    table = Base.metadata.tables[
        HEADER
    ]

    forbidden = {
        "sales_return_recognition_event_id",
        "trade_value_correction_event_id",
        "purchase_return_vat_adjustment_event_id",
        "purchase_value_correction_vat_adjustment_event_id",
        "tax_recognition_reversal_event_id",
        "tax_credit_evidence_id",
    }

    assert not (
        forbidden
        & set(table.c.keys())
    )


def test_rk_line_secondary_evidence_is_nullable():
    column = Base.metadata.tables[
        LINES
    ].c.tax_credit_evidence_id

    assert column.nullable is True
