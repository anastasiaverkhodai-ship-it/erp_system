"""add tax invoice foundation

Revision ID: c7e1f3a9b604
Revises: a4cbd7f9b268
"""

from alembic import op
import sqlalchemy as sa


revision = "c7e1f3a9b604"
down_revision = "a4cbd7f9b268"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_tax_classifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column(
            "classification_kind",
            sa.String(length=10),
            nullable=False,
        ),
        sa.Column(
            "statutory_code",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "classification_kind IN ('uktzed', 'dkpp')",
            name="ck_ptc_kind",
        ),
        sa.CheckConstraint(
            "length(trim(statutory_code)) > 0",
            name="ck_ptc_code_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_ptc_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_ptc_product",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_ptc_created_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_ptc_company_id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "product_id",
            "effective_from",
            name="uq_ptc_product_date",
        ),
    )

    op.create_index(
        "ix_ptc_product_date",
        "product_tax_classifications",
        [
            "company_id",
            "product_id",
            "effective_from",
        ],
        unique=False,
    )

    op.create_table(
        "tax_invoices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column(
            "direction",
            sa.String(length=6),
            nullable=False,
        ),
        sa.Column(
            "document_number",
            sa.String(length=120),
            nullable=False,
        ),
        sa.Column("document_date", sa.Date(), nullable=False),
        sa.Column(
            "currency_code",
            sa.String(length=3),
            server_default="UAH",
            nullable=False,
        ),
        sa.Column(
            "seller_name",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "seller_tax_number",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "seller_vat_number",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "buyer_name",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "buyer_tax_number",
            sa.String(length=20),
            nullable=True,
        ),
        sa.Column(
            "buyer_vat_number",
            sa.String(length=20),
            nullable=True,
        ),
        sa.Column(
            "source_kind",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "source_fulfillment_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "source_payment_settlement_allocation_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "source_order_vat_advance_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "direction IN ('input', 'output')",
            name="ck_ti_direction",
        ),
        sa.CheckConstraint(
            "currency_code = 'UAH'",
            name="ck_ti_uah",
        ),
        sa.CheckConstraint(
            "source_kind IN "
            "('fulfillment', 'settlement', "
            "'order_advance', 'input_external')",
            name="ck_ti_source_kind",
        ),
        sa.CheckConstraint(
            "length(trim(document_number)) > 0",
            name="ck_ti_number_nonempty",
        ),
        sa.CheckConstraint(
            "length(trim(seller_name)) > 0",
            name="ck_ti_seller_name",
        ),
        sa.CheckConstraint(
            "length(trim(seller_tax_number)) > 0",
            name="ck_ti_seller_tax",
        ),
        sa.CheckConstraint(
            "length(trim(seller_vat_number)) > 0",
            name="ck_ti_seller_vat",
        ),
        sa.CheckConstraint(
            "("
            "direction = 'input' "
            "AND source_kind = 'input_external' "
            "AND source_fulfillment_id IS NULL "
            "AND source_payment_settlement_allocation_id IS NULL "
            "AND source_order_vat_advance_id IS NULL"
            ") OR ("
            "direction = 'output' "
            "AND ("
            "("
            "source_kind = 'fulfillment' "
            "AND source_fulfillment_id IS NOT NULL "
            "AND source_payment_settlement_allocation_id IS NULL "
            "AND source_order_vat_advance_id IS NULL"
            ") OR ("
            "source_kind = 'settlement' "
            "AND source_fulfillment_id IS NULL "
            "AND source_payment_settlement_allocation_id IS NOT NULL "
            "AND source_order_vat_advance_id IS NULL"
            ") OR ("
            "source_kind = 'order_advance' "
            "AND source_fulfillment_id IS NULL "
            "AND source_payment_settlement_allocation_id IS NULL "
            "AND source_order_vat_advance_id IS NOT NULL"
            ")"
            ")"
            ")",
            name="ck_ti_source_state",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_ti_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_fulfillment_id"],
            ["trade_fulfillments.id"],
            name="fk_ti_fulfillment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "source_payment_settlement_allocation_id",
            ],
            [
                "payment_settlement_allocations.company_id",
                "payment_settlement_allocations.id",
            ],
            name="fk_ti_settlement",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "source_order_vat_advance_id",
            ],
            [
                "order_vat_advances.company_id",
                "order_vat_advances.id",
            ],
            name="fk_ti_order_advance",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_ti_created_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_ti_company_id",
        ),
    )

    op.create_index(
        "ix_ti_company_date",
        "tax_invoices",
        ["company_id", "document_date"],
        unique=False,
    )

    op.create_index(
        "ix_ti_number",
        "tax_invoices",
        ["company_id", "document_number"],
        unique=False,
    )

    op.create_index(
        "ux_ti_fulfillment_source",
        "tax_invoices",
        ["company_id", "source_fulfillment_id"],
        unique=True,
        postgresql_where=sa.text(
            "source_fulfillment_id IS NOT NULL"
        ),
    )

    op.create_index(
        "ux_ti_settlement_source",
        "tax_invoices",
        [
            "company_id",
            "source_payment_settlement_allocation_id",
        ],
        unique=True,
        postgresql_where=sa.text(
            "source_payment_settlement_allocation_id IS NOT NULL"
        ),
    )

    op.create_index(
        "ux_ti_advance_source",
        "tax_invoices",
        ["company_id", "source_order_vat_advance_id"],
        unique=True,
        postgresql_where=sa.text(
            "source_order_vat_advance_id IS NOT NULL"
        ),
    )

    op.create_table(
        "tax_invoice_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("tax_invoice_id", sa.Integer(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column(
            "tax_calculation_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "tax_recognition_event_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column(
            "description",
            sa.String(length=500),
            nullable=False,
        ),
        sa.Column(
            "quantity",
            sa.Numeric(18, 6),
            nullable=False,
        ),
        sa.Column(
            "uom_code",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "classification_kind",
            sa.String(length=10),
            nullable=False,
        ),
        sa.Column(
            "statutory_code",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "unit_price_without_vat",
            sa.Numeric(18, 6),
            nullable=False,
        ),
        sa.Column(
            "taxable_base",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "tax_rate_code",
            sa.String(length=50),
            nullable=False,
        ),
        sa.Column(
            "tax_rate",
            sa.Numeric(9, 6),
            nullable=False,
        ),
        sa.Column(
            "tax_amount",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "total_with_vat",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.CheckConstraint(
            "line_number > 0",
            name="ck_til_line_positive",
        ),
        sa.CheckConstraint(
            "length(trim(description)) > 0",
            name="ck_til_description",
        ),
        sa.CheckConstraint(
            "quantity > 0",
            name="ck_til_quantity",
        ),
        sa.CheckConstraint(
            "length(trim(uom_code)) > 0",
            name="ck_til_uom",
        ),
        sa.CheckConstraint(
            "classification_kind IN ('uktzed', 'dkpp')",
            name="ck_til_classification",
        ),
        sa.CheckConstraint(
            "length(trim(statutory_code)) > 0",
            name="ck_til_statutory_code",
        ),
        sa.CheckConstraint(
            "unit_price_without_vat >= 0",
            name="ck_til_price",
        ),
        sa.CheckConstraint(
            "taxable_base >= 0",
            name="ck_til_base",
        ),
        sa.CheckConstraint(
            "tax_rate >= 0 AND tax_rate <= 1",
            name="ck_til_rate",
        ),
        sa.CheckConstraint(
            "length(trim(tax_rate_code)) > 0",
            name="ck_til_rate_code",
        ),
        sa.CheckConstraint(
            "tax_amount >= 0",
            name="ck_til_tax",
        ),
        sa.CheckConstraint(
            "total_with_vat >= 0",
            name="ck_til_total",
        ),
        sa.CheckConstraint(
            "total_with_vat = taxable_base + tax_amount",
            name="ck_til_total_math",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_til_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_til_product",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "tax_invoice_id"],
            ["tax_invoices.company_id", "tax_invoices.id"],
            name="fk_til_invoice",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "tax_calculation_id"],
            ["tax_calculations.company_id", "tax_calculations.id"],
            name="fk_til_calculation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "tax_recognition_event_id",
                "tax_calculation_id",
            ],
            [
                "tax_recognition_events.company_id",
                "tax_recognition_events.id",
                "tax_recognition_events.tax_calculation_id",
            ],
            name="fk_til_recognition",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_til_company_id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "tax_invoice_id",
            "line_number",
            name="uq_til_invoice_line",
        ),
        sa.UniqueConstraint(
            "company_id",
            "tax_invoice_id",
            "tax_calculation_id",
            name="uq_til_invoice_calc",
        ),
    )

    op.create_index(
        "ix_til_invoice",
        "tax_invoice_lines",
        ["company_id", "tax_invoice_id"],
        unique=False,
    )

    op.create_index(
        "ix_til_calculation",
        "tax_invoice_lines",
        ["company_id", "tax_calculation_id"],
        unique=False,
    )

    op.create_index(
        "ix_til_recognition",
        "tax_invoice_lines",
        ["company_id", "tax_recognition_event_id"],
        unique=False,
    )

    op.create_index(
        "ix_til_product",
        "tax_invoice_lines",
        ["company_id", "product_id"],
        unique=False,
    )

    op.create_table(
        "tax_invoice_credit_evidence_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("tax_invoice_id", sa.Integer(), nullable=False),
        sa.Column(
            "tax_credit_evidence_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "tax_calculation_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_ticel_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "tax_invoice_id"],
            ["tax_invoices.company_id", "tax_invoices.id"],
            name="fk_ticel_invoice",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "tax_credit_evidence_id",
                "tax_calculation_id",
            ],
            [
                "tax_credit_evidence.company_id",
                "tax_credit_evidence.id",
                "tax_credit_evidence.tax_calculation_id",
            ],
            name="fk_ticel_evidence",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_ticel_company_id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "tax_credit_evidence_id",
            name="uq_ticel_evidence",
        ),
        sa.UniqueConstraint(
            "company_id",
            "tax_invoice_id",
            "tax_calculation_id",
            name="uq_ticel_invoice_calc",
        ),
    )

    op.create_index(
        "ix_ticel_invoice",
        "tax_invoice_credit_evidence_links",
        ["company_id", "tax_invoice_id"],
        unique=False,
    )

    op.create_index(
        "ix_ticel_evidence",
        "tax_invoice_credit_evidence_links",
        ["company_id", "tax_credit_evidence_id"],
        unique=False,
    )

    op.create_table(
        "tax_invoice_registration_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("tax_invoice_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column(
            "reference",
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN "
            "('prepared', 'submitted', 'registered', "
            "'suspended', 'rejected')",
            name="ck_tire_status",
        ),
        sa.CheckConstraint(
            "status = 'prepared' "
            "OR (reference IS NOT NULL "
            "AND length(trim(reference)) > 0)",
            name="ck_tire_reference",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_tire_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "tax_invoice_id"],
            ["tax_invoices.company_id", "tax_invoices.id"],
            name="fk_tire_invoice",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_tire_created_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_tire_company_id",
        ),
    )

    op.create_index(
        "ix_tire_invoice_date",
        "tax_invoice_registration_events",
        [
            "company_id",
            "tax_invoice_id",
            "event_date",
            "id",
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tire_invoice_date",
        table_name="tax_invoice_registration_events",
    )
    op.drop_table(
        "tax_invoice_registration_events"
    )

    op.drop_index(
        "ix_ticel_evidence",
        table_name="tax_invoice_credit_evidence_links",
    )
    op.drop_index(
        "ix_ticel_invoice",
        table_name="tax_invoice_credit_evidence_links",
    )
    op.drop_table(
        "tax_invoice_credit_evidence_links"
    )

    op.drop_index(
        "ix_til_product",
        table_name="tax_invoice_lines",
    )
    op.drop_index(
        "ix_til_recognition",
        table_name="tax_invoice_lines",
    )
    op.drop_index(
        "ix_til_calculation",
        table_name="tax_invoice_lines",
    )
    op.drop_index(
        "ix_til_invoice",
        table_name="tax_invoice_lines",
    )
    op.drop_table("tax_invoice_lines")

    op.drop_index(
        "ux_ti_advance_source",
        table_name="tax_invoices",
    )
    op.drop_index(
        "ux_ti_settlement_source",
        table_name="tax_invoices",
    )
    op.drop_index(
        "ux_ti_fulfillment_source",
        table_name="tax_invoices",
    )
    op.drop_index(
        "ix_ti_number",
        table_name="tax_invoices",
    )
    op.drop_index(
        "ix_ti_company_date",
        table_name="tax_invoices",
    )
    op.drop_table("tax_invoices")

    op.drop_index(
        "ix_ptc_product_date",
        table_name="product_tax_classifications",
    )
    op.drop_table(
        "product_tax_classifications"
    )
