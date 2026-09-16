import app.models  # noqa: F401

from app.core.database import Base


def _constraint_names(table):
    return {
        item.name
        for item in table.constraints
        if item.name is not None
    }


def _index_names(table):
    return {
        item.name
        for item in table.indexes
        if item.name is not None
    }


def test_tax_invoice_tables_registered():
    assert "product_tax_classifications" in Base.metadata.tables
    assert "tax_invoices" in Base.metadata.tables
    assert "tax_invoice_lines" in Base.metadata.tables
    assert (
        "tax_invoice_registration_events"
        in Base.metadata.tables
    )


def test_tax_invoice_header_contract():
    table = Base.metadata.tables["tax_invoices"]

    assert {
        "id",
        "company_id",
        "direction",
        "document_number",
        "document_date",
        "currency_code",
        "seller_name",
        "seller_tax_number",
        "seller_vat_number",
        "buyer_name",
        "buyer_tax_number",
        "buyer_vat_number",
        "source_kind",
        "source_fulfillment_id",
        "source_payment_settlement_allocation_id",
        "source_order_vat_advance_id",
        "created_by",
        "created_at",
    } == set(table.c.keys())

    names = _constraint_names(table)

    assert "uq_ti_company_id" in names
    assert "ck_ti_direction" in names
    assert "ck_ti_uah" in names
    assert "ck_ti_source_kind" in names
    assert "ck_ti_source_state" in names

    indexes = _index_names(table)

    assert {
        "ix_ti_company_date",
        "ix_ti_number",
        "ux_ti_fulfillment_source",
        "ux_ti_settlement_source",
        "ux_ti_advance_source",
    } <= indexes


def test_tax_invoice_line_is_a_snapshot():
    table = Base.metadata.tables["tax_invoice_lines"]

    assert {
        "description",
        "quantity",
        "uom_code",
        "classification_kind",
        "statutory_code",
        "unit_price_without_vat",
        "taxable_base",
        "tax_rate_code",
        "tax_rate",
        "tax_amount",
        "total_with_vat",
    } <= set(table.c.keys())

    assert table.c.tax_calculation_id.nullable is False
    assert table.c.tax_recognition_event_id.nullable is True

    names = _constraint_names(table)

    assert "uq_til_invoice_line" in names
    assert "uq_til_invoice_calc" in names
    assert "fk_til_invoice" in names
    assert "fk_til_calculation" in names
    assert "fk_til_recognition" in names


def test_registration_state_is_event_history():
    header = Base.metadata.tables["tax_invoices"]
    history = Base.metadata.tables[
        "tax_invoice_registration_events"
    ]

    assert "status" not in header.c
    assert "registration_status" not in header.c

    assert {
        "tax_invoice_id",
        "status",
        "event_date",
        "reference",
        "created_by",
        "created_at",
    } <= set(history.c.keys())

    names = _constraint_names(history)

    assert "ck_tire_status" in names
    assert "ck_tire_reference" in names
    assert "fk_tire_invoice" in names


def test_product_statutory_classification_is_dated():
    table = Base.metadata.tables[
        "product_tax_classifications"
    ]

    assert {
        "company_id",
        "product_id",
        "effective_from",
        "classification_kind",
        "statutory_code",
        "created_by",
        "created_at",
    } <= set(table.c.keys())

    names = _constraint_names(table)

    assert "uq_ptc_product_date" in names
    assert "ck_ptc_kind" in names
    assert "ck_ptc_code_nonempty" in names


def test_new_postgresql_identifiers_fit_limit():
    names = set()

    for table_name in (
        "product_tax_classifications",
        "tax_invoices",
        "tax_invoice_lines",
        "tax_invoice_registration_events",
    ):
        table = Base.metadata.tables[table_name]

        for constraint in table.constraints:
            if constraint.name:
                names.add(constraint.name)

        for index in table.indexes:
            if index.name:
                names.add(index.name)

    too_long = sorted(
        name
        for name in names
        if len(name) > 63
    )

    assert too_long == []
