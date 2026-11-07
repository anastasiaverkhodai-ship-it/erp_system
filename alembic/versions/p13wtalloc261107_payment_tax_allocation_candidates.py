"""Persist review-only payroll payment tax allocation candidates.

Revision ID: p13wtalloc261107
Revises: p13d6grant261105
"""

from alembic import op
import sqlalchemy as sa

revision = "p13wtalloc261107"
down_revision = "p13d6grant261105"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payroll_payment_tax_allocation_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("payroll_calculation_id", sa.Integer(), nullable=False),
        sa.Column("payroll_disbursement_id", sa.Integer(), nullable=False),
        sa.Column("statutory_line_id", sa.Integer(), nullable=False),
        sa.Column("component", sa.String(64), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("request_key", sa.String(150), nullable=False),
        sa.Column("evidence_reference", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="draft"),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "payroll_calculation_id"],
            ["payroll_calculations.company_id", "payroll_calculations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "payroll_disbursement_id"],
            ["payroll_disbursements.company_id", "payroll_disbursements.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "statutory_line_id"],
            ["payroll_statutory_result_lines.company_id",
             "payroll_statutory_result_lines.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id", "id", name="uq_pptac_company_id",
        ),
        sa.UniqueConstraint(
            "company_id", "request_key", name="uq_pptac_request",
        ),
        sa.UniqueConstraint(
            "company_id", "payroll_disbursement_id",
            "statutory_line_id", name="uq_pptac_payment_statutory",
        ),
        sa.CheckConstraint(
            "component IN ('personal_income_tax','military_levy')",
            name="ck_pptac_component",
        ),
        sa.CheckConstraint(
            "amount > 0 AND amount <> 'NaN'::numeric",
            name="ck_pptac_positive_amount",
        ),
        sa.CheckConstraint(
            "status = 'draft'", name="ck_pptac_draft_only",
        ),
        sa.CheckConstraint(
            "length(btrim(request_key)) > 0 "
            "AND length(btrim(evidence_reference)) > 0",
            name="ck_pptac_references",
        ),
    )
    op.create_index(
        "ix_pptac_company_calculation",
        "payroll_payment_tax_allocation_candidates",
        ["company_id", "payroll_calculation_id"],
    )


def downgrade():
    op.drop_index(
        "ix_pptac_company_calculation",
        table_name="payroll_payment_tax_allocation_candidates",
    )
    op.drop_table("payroll_payment_tax_allocation_candidates")
