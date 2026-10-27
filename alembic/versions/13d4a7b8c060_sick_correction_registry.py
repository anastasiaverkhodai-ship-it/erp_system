"""Sick correction pending-review registry.

Revision ID: 13d4a7b8c060
Revises: 13d4a7b8c059
"""
from alembic import op
import sqlalchemy as sa

revision = "13d4a7b8c060"
down_revision = "13d4a7b8c059"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payroll_sick_corrections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column(
            "original_calculation_id",
            sa.Integer(), nullable=False
        ),
        sa.Column(
            "insurance_evidence_id",
            sa.Integer(), nullable=False
        ),
        sa.Column(
            "request_key", sa.String(100), nullable=False
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "original_amount",
            sa.Numeric(18, 2), nullable=False
        ),
        sa.Column(
            "status", sa.String(30),
            nullable=False,
            server_default="pending_review",
        ),
        sa.Column(
            "requested_by", sa.Integer(), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "original_calculation_id"],
            [
                "payroll_sick_leave_calculations.company_id",
                "payroll_sick_leave_calculations.id",
            ],
            name="fk_sick_correction_original",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "insurance_evidence_id"],
            [
                "payroll_insurance_evidence.company_id",
                "payroll_insurance_evidence.id",
            ],
            name="fk_sick_correction_evidence",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id", "request_key",
            name="uq_sick_correction_request",
        ),
        sa.UniqueConstraint(
            "company_id", "original_calculation_id",
            name="uq_sick_correction_original",
        ),
        sa.CheckConstraint(
            "status = 'pending_review'",
            name="ck_sick_correction_pending",
        ),
        sa.CheckConstraint(
            "original_amount >= 0",
            name="ck_sick_correction_original_amount",
        ),
        sa.CheckConstraint(
            "length(trim(reason)) >= 10",
            name="ck_sick_correction_reason",
        ),
    )


def downgrade():
    bind = op.get_bind()
    count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM payroll_sick_corrections"
        )
    ).scalar_one()

    if count:
        raise RuntimeError(
            "Cannot downgrade: historical sick corrections exist"
        )

    op.drop_table("payroll_sick_corrections")
