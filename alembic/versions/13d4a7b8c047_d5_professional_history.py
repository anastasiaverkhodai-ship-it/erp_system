
"""Add documented professional evidence for D5 preparation.

Revision ID: 13d4a7b8c047
Revises: 13d4a7b8c046
"""
from alembic import op
import sqlalchemy as sa

revision = "13d4a7b8c047"
down_revision = "13d4a7b8c046"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payroll_d5_professional_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("employment_contract_id", sa.Integer(), nullable=False),
        sa.Column("employment_event_id", sa.Integer(), nullable=False),
        sa.Column("position_id", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("documented_on", sa.Date(), nullable=False),
        sa.Column("professional_name", sa.String(250), nullable=False),
        sa.Column("profession_code", sa.String(30), nullable=False),
        sa.Column("position_name", sa.String(250), nullable=False),
        sa.Column("employment_category", sa.String(30), nullable=False),
        sa.Column("evidence_reference", sa.String(500), nullable=False),
        sa.Column("request_key", sa.String(200), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "company_id", "id",
            name="uq_d5_prof_evidence_company_id",
        ),
        sa.UniqueConstraint(
            "company_id", "request_key",
            name="uq_d5_prof_evidence_request",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "employment_contract_id"],
            ["employment_contracts.company_id", "employment_contracts.id"],
            name="fk_d5_prof_evidence_contract",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "employment_event_id"],
            ["employment_events.company_id", "employment_events.id"],
            name="fk_d5_prof_evidence_event",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "position_id"],
            ["positions.company_id", "positions.id"],
            name="fk_d5_prof_evidence_position",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "company_id > 0 AND employee_id > 0 "
            "AND employment_contract_id > 0 "
            "AND employment_event_id > 0 AND position_id > 0",
            name="ck_d5_prof_evidence_positive_ids",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_d5_prof_evidence_dates",
        ),
        sa.CheckConstraint(
            "employment_category IN "
            "('primary','internal_secondary','external_secondary')",
            name="ck_d5_prof_evidence_category",
        ),
    )


def downgrade():
    # Never delete preserved professional evidence silently.
    bind = op.get_bind()
    count = bind.execute(
        sa.text("SELECT count(*) FROM payroll_d5_professional_evidence")
    ).scalar_one()
    if count:
        raise RuntimeError(
            "Cannot downgrade: documented D5 history would be lost"
        )
    op.drop_table("payroll_d5_professional_evidence")
