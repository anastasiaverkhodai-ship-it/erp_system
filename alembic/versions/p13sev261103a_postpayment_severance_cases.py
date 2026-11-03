"""Persist documented postpayment severance correction cases.

Revision ID: p13sev261103a
Revises: p13rpt261102a
"""

from alembic import op
import sqlalchemy as sa


revision = "p13sev261103a"
down_revision = "p13rpt261102a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payroll_severance_correction_cases",
        sa.Column(
            "id", sa.Integer(),
            sa.Identity(), primary_key=True,
        ),
        sa.Column(
            "company_id", sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "payroll_input_id", sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "original_payroll_calculation_id",
            sa.Integer(), nullable=False,
        ),
        sa.Column(
            "original_severance_source_id",
            sa.Integer(), nullable=False,
        ),
        sa.Column(
            "request_key", sa.String(120),
            nullable=False,
        ),
        sa.Column(
            "request_fingerprint", sa.String(64),
            nullable=False,
        ),
        sa.Column(
            "evidence_reference", sa.String(500),
            nullable=False,
        ),
        sa.Column(
            "evidence_date", sa.Date(),
            nullable=False,
        ),
        sa.Column(
            "original_severance_gross",
            sa.Numeric(18, 2), nullable=False,
        ),
        sa.Column(
            "posted_payroll_amount_at_opening",
            sa.Numeric(18, 2), nullable=False,
        ),
        sa.Column(
            "as_of_date", sa.Date(),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(32),
            nullable=False,
            server_default="review_required",
        ),
        sa.Column(
            "created_by", sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "original_payroll_calculation_id"],
            [
                "payroll_calculations.company_id",
                "payroll_calculations.id",
            ],
            name="fk_sev_case_payroll_calculation",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id", "request_key",
            name="uq_sev_case_company_request",
        ),
        sa.CheckConstraint(
            "original_severance_gross > 0 "
            "AND posted_payroll_amount_at_opening > 0",
            name="ck_sev_case_positive_financial_sources",
        ),
        sa.CheckConstraint(
            "status = 'review_required'",
            name="ck_sev_case_review_only",
        ),
        sa.CheckConstraint(
            "length(trim(evidence_reference)) >= 10",
            name="ck_sev_case_document_reference",
        ),
    )

    op.create_index(
        "uq_sev_case_active_calculation",
        "payroll_severance_correction_cases",
        ["company_id", "original_payroll_calculation_id"],
        unique=True,
        postgresql_where=sa.text(
            "status = 'review_required'"
        ),
    )


def downgrade():
    op.drop_index(
        "uq_sev_case_active_calculation",
        table_name="payroll_severance_correction_cases",
    )
    op.drop_table(
        "payroll_severance_correction_cases"
    )
