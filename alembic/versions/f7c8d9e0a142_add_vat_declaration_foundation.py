"""add VAT declaration foundation

Revision ID: f7c8d9e0a142
Revises: e6b4a9c2d731
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7c8d9e0a142"
down_revision: Union[str, Sequence[str], None] = "e6b4a9c2d731"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vat_declarations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("reporting_year", sa.Integer(), nullable=False),
        sa.Column("reporting_month", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column(
            "source_cutoff_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("snapshot_version", sa.Integer(), nullable=False),
        sa.Column(
            "supersedes_declaration_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "output_taxable_base",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "output_vat",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "input_taxable_base",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "input_vat_credit",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "opening_negative_carry",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "vat_payable",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "current_period_negative",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "closing_negative_carry",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "currency_code",
            sa.String(length=3),
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
            "reporting_month BETWEEN 1 AND 12",
            name="ck_vd_month",
        ),
        sa.CheckConstraint(
            "snapshot_version >= 1",
            name="ck_vd_version",
        ),
        sa.CheckConstraint(
            "period_start <= period_end",
            name="ck_vd_period_order",
        ),
        sa.CheckConstraint(
            "currency_code = 'UAH'",
            name="ck_vd_uah",
        ),
        sa.CheckConstraint(
            "output_taxable_base >= 0 "
            "AND output_vat >= 0 "
            "AND input_taxable_base >= 0 "
            "AND input_vat_credit >= 0 "
            "AND opening_negative_carry >= 0 "
            "AND vat_payable >= 0 "
            "AND current_period_negative >= 0 "
            "AND closing_negative_carry >= 0",
            name="ck_vd_amounts_nonneg",
        ),
        sa.CheckConstraint(
            "supersedes_declaration_id IS NULL "
            "OR supersedes_declaration_id <> id",
            name="ck_vd_no_self_supersede",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_vd_company",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_vd_created_by",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "supersedes_declaration_id"],
            ["vat_declarations.company_id", "vat_declarations.id"],
            name="fk_vd_supersedes_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_vd_company_id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "reporting_year",
            "reporting_month",
            "snapshot_version",
            name="uq_vd_period_version",
        ),
    )
    op.create_index(
        "ix_vd_company",
        "vat_declarations",
        ["company_id"],
        unique=False,
    )

    op.create_table(
        "vat_declaration_source_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("vat_declaration_id", sa.Integer(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column(
            "source_kind",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "tax_recognition_event_id",
            sa.Integer(),
            nullable=True,
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
            "purchase_return_input_vat_credit_correction_event_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "purchase_value_correction_input_vat_credit_correction_event_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "economic_effective_date",
            sa.Date(),
            nullable=False,
        ),
        sa.Column(
            "direction",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "taxable_base_delta",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "tax_amount_delta",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "currency_code",
            sa.String(length=3),
            nullable=False,
        ),
        sa.Column(
            "source_reversal_of_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "tax_rate_code",
            sa.String(length=32),
            nullable=True,
        ),
        sa.Column(
            "tax_rate",
            sa.Numeric(9, 6),
            nullable=True,
        ),
        sa.CheckConstraint(
            "line_number >= 1",
            name="ck_vdsl_line_no",
        ),
        sa.CheckConstraint(
            "source_kind IN ("
            "'output_tax_recognition', "
            "'input_tax_recognition', "
            "'sales_return', "
            "'sales_value_correction', "
            "'purchase_return_input_credit_correction', "
            "'purchase_value_input_credit_correction'"
            ")",
            name="ck_vdsl_source_kind",
        ),
        sa.CheckConstraint(
            "direction IN ('output', 'input')",
            name="ck_vdsl_direction",
        ),
        sa.CheckConstraint(
            "currency_code = 'UAH'",
            name="ck_vdsl_uah",
        ),
        sa.CheckConstraint(
            "taxable_base_delta <> 0 OR tax_amount_delta <> 0",
            name="ck_vdsl_nonzero",
        ),
        sa.CheckConstraint(
            "num_nonnulls("
            "tax_recognition_event_id, "
            "sales_return_recognition_event_id, "
            "trade_value_correction_event_id, "
            "purchase_return_input_vat_credit_correction_event_id, "
            "purchase_value_correction_input_vat_credit_correction_event_id"
            ") = 1",
            name="ck_vdsl_one_source",
        ),
        sa.CheckConstraint(
            "("
            "source_kind IN ('output_tax_recognition', 'input_tax_recognition') "
            "AND tax_recognition_event_id IS NOT NULL"
            ") OR ("
            "source_kind = 'sales_return' "
            "AND sales_return_recognition_event_id IS NOT NULL"
            ") OR ("
            "source_kind = 'sales_value_correction' "
            "AND trade_value_correction_event_id IS NOT NULL"
            ") OR ("
            "source_kind = 'purchase_return_input_credit_correction' "
            "AND purchase_return_input_vat_credit_correction_event_id IS NOT NULL"
            ") OR ("
            "source_kind = 'purchase_value_input_credit_correction' "
            "AND purchase_value_correction_input_vat_credit_correction_event_id "
            "IS NOT NULL"
            ")",
            name="ck_vdsl_kind_fk",
        ),
        sa.CheckConstraint(
            "("
            "source_kind IN ("
            "'output_tax_recognition', "
            "'sales_return', "
            "'sales_value_correction'"
            ") AND direction = 'output'"
            ") OR ("
            "source_kind IN ("
            "'input_tax_recognition', "
            "'purchase_return_input_credit_correction', "
            "'purchase_value_input_credit_correction'"
            ") AND direction = 'input'"
            ")",
            name="ck_vdsl_kind_dir",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "vat_declaration_id"],
            ["vat_declarations.company_id", "vat_declarations.id"],
            name="fk_vdsl_decl_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "tax_recognition_event_id"],
            ["tax_recognition_events.company_id", "tax_recognition_events.id"],
            name="fk_vdsl_taxrec_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "sales_return_recognition_event_id"],
            [
                "sales_return_recognition_events.company_id",
                "sales_return_recognition_events.id",
            ],
            name="fk_vdsl_sret_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "trade_value_correction_event_id"],
            [
                "trade_value_correction_events.company_id",
                "trade_value_correction_events.id",
            ],
            name="fk_vdsl_tvc_tenant",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "purchase_return_input_vat_credit_correction_event_id",
            ],
            [
                "purchase_return_input_vat_credit_correction_events.company_id",
                "purchase_return_input_vat_credit_correction_events.id",
            ],
            name="fk_vdsl_pricc_tenant",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "purchase_value_correction_input_vat_credit_correction_event_id",
            ],
            [
                "purchase_value_correction_input_vat_credit_correction_events.company_id",
                "purchase_value_correction_input_vat_credit_correction_events.id",
            ],
            name="fk_vdsl_pvicc_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_vdsl_company_id",
        ),
        sa.UniqueConstraint(
            "vat_declaration_id",
            "line_number",
            name="uq_vdsl_decl_line",
        ),
        sa.UniqueConstraint(
            "vat_declaration_id",
            "tax_recognition_event_id",
            name="uq_vdsl_decl_taxrec",
        ),
        sa.UniqueConstraint(
            "vat_declaration_id",
            "sales_return_recognition_event_id",
            name="uq_vdsl_decl_sret",
        ),
        sa.UniqueConstraint(
            "vat_declaration_id",
            "trade_value_correction_event_id",
            name="uq_vdsl_decl_tvc",
        ),
        sa.UniqueConstraint(
            "vat_declaration_id",
            "purchase_return_input_vat_credit_correction_event_id",
            name="uq_vdsl_decl_pricc",
        ),
        sa.UniqueConstraint(
            "vat_declaration_id",
            "purchase_value_correction_input_vat_credit_correction_event_id",
            name="uq_vdsl_decl_pvicc",
        ),
    )
    op.create_index(
        "ix_vdsl_company",
        "vat_declaration_source_lines",
        ["company_id"],
        unique=False,
    )

    op.create_table(
        "vat_declaration_carry_forward_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("vat_declaration_id", sa.Integer(), nullable=False),
        sa.Column("source_declaration_id", sa.Integer(), nullable=False),
        sa.Column(
            "origin_reporting_year",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "origin_reporting_month",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "opening_amount",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "consumed_amount",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "closing_amount",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.CheckConstraint(
            "origin_reporting_month BETWEEN 1 AND 12",
            name="ck_vdcf_origin_month",
        ),
        sa.CheckConstraint(
            "opening_amount >= 0 "
            "AND consumed_amount >= 0 "
            "AND closing_amount >= 0",
            name="ck_vdcf_nonneg",
        ),
        sa.CheckConstraint(
            "consumed_amount <= opening_amount",
            name="ck_vdcf_consumed",
        ),
        sa.CheckConstraint(
            "closing_amount = opening_amount - consumed_amount",
            name="ck_vdcf_arithmetic",
        ),
        sa.CheckConstraint(
            "source_declaration_id <> vat_declaration_id",
            name="ck_vdcf_not_self",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "vat_declaration_id"],
            ["vat_declarations.company_id", "vat_declarations.id"],
            name="fk_vdcf_decl_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "source_declaration_id"],
            ["vat_declarations.company_id", "vat_declarations.id"],
            name="fk_vdcf_source_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_vdcf_company_id",
        ),
        sa.UniqueConstraint(
            "vat_declaration_id",
            "origin_reporting_year",
            "origin_reporting_month",
            name="uq_vdcf_decl_origin",
        ),
    )
    op.create_index(
        "ix_vdcf_company",
        "vat_declaration_carry_forward_lines",
        ["company_id"],
        unique=False,
    )

    op.create_table(
        "vat_declaration_status_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("vat_declaration_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column(
            "reference",
            sa.String(length=255),
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
            "status IN ("
            "'prepared', "
            "'finalized', "
            "'submitted', "
            "'accepted', "
            "'rejected'"
            ")",
            name="ck_vdse_status",
        ),
        sa.CheckConstraint(
            "status NOT IN ('submitted', 'accepted', 'rejected') "
            "OR (reference IS NOT NULL AND btrim(reference) <> '')",
            name="ck_vdse_reference",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "vat_declaration_id"],
            ["vat_declarations.company_id", "vat_declarations.id"],
            name="fk_vdse_decl_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_vdse_created_by",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_vdse_company_id",
        ),
    )
    op.create_index(
        "ix_vdse_company",
        "vat_declaration_status_events",
        ["company_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_vdse_company",
        table_name="vat_declaration_status_events",
    )
    op.drop_table("vat_declaration_status_events")

    op.drop_index(
        "ix_vdcf_company",
        table_name="vat_declaration_carry_forward_lines",
    )
    op.drop_table("vat_declaration_carry_forward_lines")

    op.drop_index(
        "ix_vdsl_company",
        table_name="vat_declaration_source_lines",
    )
    op.drop_table("vat_declaration_source_lines")

    op.drop_index(
        "ix_vd_company",
        table_name="vat_declarations",
    )
    op.drop_table("vat_declarations")
