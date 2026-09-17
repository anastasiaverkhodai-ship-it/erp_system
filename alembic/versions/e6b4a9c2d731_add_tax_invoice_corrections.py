"""add canonical tax invoice corrections

Revision ID: e6b4a9c2d731
Revises: c7e1f3a9b604
"""

from alembic import op
import sqlalchemy as sa


revision = "e6b4a9c2d731"
down_revision = "c7e1f3a9b604"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tax_invoice_corrections",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "original_tax_invoice_id",
            sa.Integer(),
            nullable=False,
        ),
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
        sa.Column(
            "document_date",
            sa.Date(),
            nullable=False,
        ),
        sa.Column(
            "currency_code",
            sa.String(length=3),
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
            "created_by",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "direction IN ('input', 'output')",
            name="ck_tic_direction",
        ),
        sa.CheckConstraint(
            "currency_code = 'UAH'",
            name="ck_tic_uah",
        ),
        sa.CheckConstraint(
            "length(trim(document_number)) > 0",
            name="ck_tic_number",
        ),
        sa.CheckConstraint(
            "length(trim(seller_name)) > 0",
            name="ck_tic_seller_name",
        ),
        sa.CheckConstraint(
            "length(trim(seller_tax_number)) > 0",
            name="ck_tic_seller_tax",
        ),
        sa.CheckConstraint(
            "length(trim(seller_vat_number)) > 0",
            name="ck_tic_seller_vat",
        ),
        sa.CheckConstraint(
            """
            (
                buyer_name IS NULL
                AND buyer_tax_number IS NULL
                AND buyer_vat_number IS NULL
            )
            OR
            (
                length(trim(buyer_name)) > 0
                AND length(trim(buyer_tax_number)) > 0
                AND length(trim(buyer_vat_number)) > 0
            )
            """,
            name="ck_tic_buyer_shape",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_tic_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "original_tax_invoice_id",
            ],
            [
                "tax_invoices.company_id",
                "tax_invoices.id",
            ],
            name="fk_tic_original_invoice",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_tic_created_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_tic_company_id",
        ),
    )

    op.create_index(
        "ix_tic_company_date",
        "tax_invoice_corrections",
        [
            "company_id",
            "document_date",
            "id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_tic_original_invoice",
        "tax_invoice_corrections",
        [
            "company_id",
            "original_tax_invoice_id",
            "id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_tic_number",
        "tax_invoice_corrections",
        [
            "company_id",
            "document_number",
        ],
        unique=False,
    )

    op.create_table(
        "tax_invoice_correction_lines",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "tax_invoice_correction_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "line_number",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "original_tax_invoice_line_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "source_kind",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "sales_return_recognition_event_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "trade_value_correction_event_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "purchase_return_vat_adjustment_event_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "purchase_value_correction_vat_adjustment_event_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "tax_recognition_reversal_event_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "tax_credit_evidence_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "reason_code",
            sa.String(length=40),
            nullable=False,
        ),
        sa.Column(
            "description",
            sa.String(length=500),
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
            "tax_rate_code",
            sa.String(length=50),
            nullable=False,
        ),
        sa.Column(
            "tax_rate",
            sa.Numeric(
                precision=9,
                scale=6,
            ),
            nullable=False,
        ),
        sa.Column(
            "quantity_delta",
            sa.Numeric(
                precision=18,
                scale=6,
            ),
            nullable=False,
        ),
        sa.Column(
            "unit_price_without_vat_delta",
            sa.Numeric(
                precision=18,
                scale=6,
            ),
            nullable=False,
        ),
        sa.Column(
            "taxable_base_delta",
            sa.Numeric(
                precision=18,
                scale=2,
            ),
            nullable=False,
        ),
        sa.Column(
            "tax_amount_delta",
            sa.Numeric(
                precision=18,
                scale=2,
            ),
            nullable=False,
        ),
        sa.Column(
            "total_with_vat_delta",
            sa.Numeric(
                precision=18,
                scale=2,
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            """
            source_kind IN (
                'sales_return',
                'sales_value_correction',
                'purchase_return',
                'purchase_value_correction',
                'recognition_reversal'
            )
            """,
            name="ck_ticl_source_kind",
        ),
        sa.CheckConstraint(
            """
            (
                source_kind = 'sales_return'
                AND sales_return_recognition_event_id IS NOT NULL
                AND trade_value_correction_event_id IS NULL
                AND purchase_return_vat_adjustment_event_id IS NULL
                AND purchase_value_correction_vat_adjustment_event_id IS NULL
                AND tax_recognition_reversal_event_id IS NULL
            )
            OR
            (
                source_kind = 'sales_value_correction'
                AND sales_return_recognition_event_id IS NULL
                AND trade_value_correction_event_id IS NOT NULL
                AND purchase_return_vat_adjustment_event_id IS NULL
                AND purchase_value_correction_vat_adjustment_event_id IS NULL
                AND tax_recognition_reversal_event_id IS NULL
            )
            OR
            (
                source_kind = 'purchase_return'
                AND sales_return_recognition_event_id IS NULL
                AND trade_value_correction_event_id IS NULL
                AND purchase_return_vat_adjustment_event_id IS NOT NULL
                AND purchase_value_correction_vat_adjustment_event_id IS NULL
                AND tax_recognition_reversal_event_id IS NULL
            )
            OR
            (
                source_kind = 'purchase_value_correction'
                AND sales_return_recognition_event_id IS NULL
                AND trade_value_correction_event_id IS NULL
                AND purchase_return_vat_adjustment_event_id IS NULL
                AND purchase_value_correction_vat_adjustment_event_id IS NOT NULL
                AND tax_recognition_reversal_event_id IS NULL
            )
            OR
            (
                source_kind = 'recognition_reversal'
                AND sales_return_recognition_event_id IS NULL
                AND trade_value_correction_event_id IS NULL
                AND purchase_return_vat_adjustment_event_id IS NULL
                AND purchase_value_correction_vat_adjustment_event_id IS NULL
                AND tax_recognition_reversal_event_id IS NOT NULL
            )
            """,
            name="ck_ticl_source_shape",
        ),
        sa.CheckConstraint(
            "line_number > 0",
            name="ck_ticl_line_positive",
        ),
        sa.CheckConstraint(
            "length(trim(reason_code)) > 0",
            name="ck_ticl_reason",
        ),
        sa.CheckConstraint(
            "length(trim(description)) > 0",
            name="ck_ticl_description",
        ),
        sa.CheckConstraint(
            "length(trim(uom_code)) > 0",
            name="ck_ticl_uom",
        ),
        sa.CheckConstraint(
            "classification_kind IN ('uktzed', 'dkpp')",
            name="ck_ticl_classification",
        ),
        sa.CheckConstraint(
            "length(trim(statutory_code)) > 0",
            name="ck_ticl_statutory_code",
        ),
        sa.CheckConstraint(
            "length(trim(tax_rate_code)) > 0",
            name="ck_ticl_rate_code",
        ),
        sa.CheckConstraint(
            "tax_rate >= 0",
            name="ck_ticl_rate",
        ),
        sa.CheckConstraint(
            """
            quantity_delta <> 0
            OR unit_price_without_vat_delta <> 0
            OR taxable_base_delta <> 0
            OR tax_amount_delta <> 0
            OR total_with_vat_delta <> 0
            """,
            name="ck_ticl_nonzero_delta",
        ),
        sa.CheckConstraint(
            """
            total_with_vat_delta
            = taxable_base_delta + tax_amount_delta
            """,
            name="ck_ticl_total_math",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_ticl_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "tax_invoice_correction_id",
            ],
            [
                "tax_invoice_corrections.company_id",
                "tax_invoice_corrections.id",
            ],
            name="fk_ticl_correction",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "original_tax_invoice_line_id",
            ],
            [
                "tax_invoice_lines.company_id",
                "tax_invoice_lines.id",
            ],
            name="fk_ticl_original_line",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "sales_return_recognition_event_id",
            ],
            [
                "sales_return_recognition_events.company_id",
                "sales_return_recognition_events.id",
            ],
            name="fk_ticl_sales_return",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "trade_value_correction_event_id",
            ],
            [
                "trade_value_correction_events.company_id",
                "trade_value_correction_events.id",
            ],
            name="fk_ticl_value_correction",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "purchase_return_vat_adjustment_event_id",
            ],
            [
                "purchase_return_vat_adjustment_events.company_id",
                "purchase_return_vat_adjustment_events.id",
            ],
            name="fk_ticl_purchase_return",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "purchase_value_correction_vat_adjustment_event_id",
            ],
            [
                "purchase_value_correction_vat_adjustment_events.company_id",
                "purchase_value_correction_vat_adjustment_events.id",
            ],
            name="fk_ticl_purchase_value",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "tax_recognition_reversal_event_id",
            ],
            [
                "tax_recognition_events.company_id",
                "tax_recognition_events.id",
            ],
            name="fk_ticl_recognition_reversal",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "tax_credit_evidence_id",
            ],
            [
                "tax_credit_evidence.company_id",
                "tax_credit_evidence.id",
            ],
            name="fk_ticl_credit_evidence",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_ticl_company_id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "tax_invoice_correction_id",
            "line_number",
            name="uq_ticl_correction_line",
        ),
    )

    op.create_index(
        "ix_ticl_correction",
        "tax_invoice_correction_lines",
        [
            "company_id",
            "tax_invoice_correction_id",
            "line_number",
        ],
        unique=False,
    )

    op.create_index(
        "ix_ticl_original_line",
        "tax_invoice_correction_lines",
        [
            "company_id",
            "original_tax_invoice_line_id",
        ],
        unique=False,
    )

    op.create_index(
        "ux_ticl_sales_return_source",
        "tax_invoice_correction_lines",
        [
            "company_id",
            "sales_return_recognition_event_id",
        ],
        unique=True,
        postgresql_where=sa.text(
            "sales_return_recognition_event_id IS NOT NULL"
        ),
    )

    op.create_index(
        "ux_ticl_value_source",
        "tax_invoice_correction_lines",
        [
            "company_id",
            "trade_value_correction_event_id",
        ],
        unique=True,
        postgresql_where=sa.text(
            "trade_value_correction_event_id IS NOT NULL"
        ),
    )

    op.create_index(
        "ux_ticl_purchase_return_source",
        "tax_invoice_correction_lines",
        [
            "company_id",
            "purchase_return_vat_adjustment_event_id",
        ],
        unique=True,
        postgresql_where=sa.text(
            "purchase_return_vat_adjustment_event_id IS NOT NULL"
        ),
    )

    op.create_index(
        "ux_ticl_purchase_value_source",
        "tax_invoice_correction_lines",
        [
            "company_id",
            "purchase_value_correction_vat_adjustment_event_id",
        ],
        unique=True,
        postgresql_where=sa.text(
            "purchase_value_correction_vat_adjustment_event_id IS NOT NULL"
        ),
    )

    op.create_index(
        "ux_ticl_recognition_reversal_source",
        "tax_invoice_correction_lines",
        [
            "company_id",
            "tax_recognition_reversal_event_id",
        ],
        unique=True,
        postgresql_where=sa.text(
            "tax_recognition_reversal_event_id IS NOT NULL"
        ),
    )

    op.create_index(
        "ix_ticl_credit_evidence",
        "tax_invoice_correction_lines",
        [
            "company_id",
            "tax_credit_evidence_id",
        ],
        unique=False,
    )

    op.create_table(
        "tax_invoice_correction_registration_events",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "tax_invoice_correction_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "event_date",
            sa.Date(),
            nullable=False,
        ),
        sa.Column(
            "reference",
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            """
            status IN (
                'prepared',
                'submitted',
                'registered',
                'suspended',
                'rejected'
            )
            """,
            name="ck_ticre_status",
        ),
        sa.CheckConstraint(
            """
            status = 'prepared'
            OR (
                reference IS NOT NULL
                AND length(trim(reference)) > 0
            )
            """,
            name="ck_ticre_reference",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_ticre_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "tax_invoice_correction_id",
            ],
            [
                "tax_invoice_corrections.company_id",
                "tax_invoice_corrections.id",
            ],
            name="fk_ticre_correction",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_ticre_created_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_ticre_company_id",
        ),
    )

    op.create_index(
        "ix_ticre_correction_date",
        "tax_invoice_correction_registration_events",
        [
            "company_id",
            "tax_invoice_correction_id",
            "event_date",
            "id",
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ticre_correction_date",
        table_name="tax_invoice_correction_registration_events",
    )
    op.drop_table(
        "tax_invoice_correction_registration_events"
    )

    op.drop_index(
        "ix_ticl_credit_evidence",
        table_name="tax_invoice_correction_lines",
    )
    op.drop_index(
        "ux_ticl_recognition_reversal_source",
        table_name="tax_invoice_correction_lines",
    )
    op.drop_index(
        "ux_ticl_purchase_value_source",
        table_name="tax_invoice_correction_lines",
    )
    op.drop_index(
        "ux_ticl_purchase_return_source",
        table_name="tax_invoice_correction_lines",
    )
    op.drop_index(
        "ux_ticl_value_source",
        table_name="tax_invoice_correction_lines",
    )
    op.drop_index(
        "ux_ticl_sales_return_source",
        table_name="tax_invoice_correction_lines",
    )
    op.drop_index(
        "ix_ticl_original_line",
        table_name="tax_invoice_correction_lines",
    )
    op.drop_index(
        "ix_ticl_correction",
        table_name="tax_invoice_correction_lines",
    )
    op.drop_table(
        "tax_invoice_correction_lines"
    )

    op.drop_index(
        "ix_tic_number",
        table_name="tax_invoice_corrections",
    )
    op.drop_index(
        "ix_tic_original_invoice",
        table_name="tax_invoice_corrections",
    )
    op.drop_index(
        "ix_tic_company_date",
        table_name="tax_invoice_corrections",
    )
    op.drop_table(
        "tax_invoice_corrections"
    )
