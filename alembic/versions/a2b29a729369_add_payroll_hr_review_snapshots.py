"""Persist immutable D5/D6 internal HR review snapshots.

Revision ID: a2b29a729369
Revises: 13d4a7b8c070
"""

from alembic import op
import sqlalchemy as sa

revision = "a2b29a729369"
down_revision = "13d4a7b8c070"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payroll_hr_review_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("payroll_period_id", sa.Integer(), nullable=False),
        sa.Column("payroll_regulatory_report_id", sa.Integer(), nullable=False),
        sa.Column("report_kind", sa.String(16), nullable=False),
        sa.Column("report_revision", sa.Integer(), nullable=False),
        sa.Column("previous_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "payroll_regulatory_report_id"],
            [
                "payroll_regulatory_reports.company_id",
                "payroll_regulatory_reports.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "payroll_period_id"],
            ["payroll_periods.company_id", "payroll_periods.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "report_kind IN ('d5', 'd6')",
            name="ck_payroll_hr_review_snapshot_kind",
        ),
        sa.CheckConstraint(
            "report_revision > 0",
            name="ck_payroll_hr_review_snapshot_revision",
        ),
        sa.CheckConstraint(
            "snapshot_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_payroll_hr_review_snapshot_hash",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_hr_review_snapshot_company_id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "payroll_regulatory_report_id",
            name="uq_payroll_hr_review_snapshot_report",
        ),
    )

    op.create_foreign_key(
        "fk_payroll_hr_review_snapshot_previous",
        "payroll_hr_review_snapshots",
        "payroll_hr_review_snapshots",
        ["company_id", "previous_snapshot_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )

    op.create_index(
        "ix_payroll_hr_review_snapshot_period_kind",
        "payroll_hr_review_snapshots",
        ["company_id", "payroll_period_id", "report_kind", "report_revision"],
    )

    op.execute("""
        CREATE FUNCTION protect_payroll_hr_review_snapshot()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'Payroll HR review snapshots are append-only'
                USING ERRCODE = 'P0001';
        END;
        $$ LANGUAGE plpgsql
    """)

    op.execute("""
        CREATE TRIGGER trg_payroll_hr_review_snapshot_immutable
        BEFORE UPDATE OR DELETE
        ON payroll_hr_review_snapshots
        FOR EACH ROW
        EXECUTE FUNCTION protect_payroll_hr_review_snapshot()
    """)


def downgrade():
    op.execute("""
        DROP TRIGGER IF EXISTS
        trg_payroll_hr_review_snapshot_immutable
        ON payroll_hr_review_snapshots
    """)
    op.execute(
        "DROP FUNCTION IF EXISTS protect_payroll_hr_review_snapshot()"
    )
    op.drop_index(
        "ix_payroll_hr_review_snapshot_period_kind",
        table_name="payroll_hr_review_snapshots",
    )
    op.drop_table("payroll_hr_review_snapshots")
