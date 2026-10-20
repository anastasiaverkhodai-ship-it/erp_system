"""Immutable documented severance source, additive foundation.

Revision ID: 13d4a7b8c040
Revises: 13d4a7b8c039
"""

from alembic import op
import sqlalchemy as sa


revision = "13d4a7b8c040"
down_revision = "13d4a7b8c039"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payroll_termination_severances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("payroll_input_id", sa.Integer(), nullable=False),
        sa.Column("employment_contract_id", sa.Integer(), nullable=False),
        sa.Column("termination_event_id", sa.Integer(), nullable=False),
        sa.Column(
            "revision", sa.Integer(),
            nullable=False, server_default="1",
        ),
        sa.Column("supersedes_id", sa.Integer(), nullable=True),
        sa.Column("request_key", sa.String(200), nullable=False),
        sa.Column("termination_date", sa.Date(), nullable=False),
        sa.Column("order_number", sa.String(100), nullable=False),
        sa.Column("policy_ground", sa.String(64), nullable=False),
        sa.Column("policy_category", sa.String(32), nullable=False),
        sa.Column(
            "labour_code_provision",
            sa.String(100), nullable=False,
        ),
        sa.Column(
            "source_reference", sa.String(1000), nullable=False,
        ),
        sa.Column("multiplier", sa.Integer(), nullable=False),
        sa.Column("basis_as_of", sa.Date(), nullable=False),
        sa.Column(
            "basis_reference", sa.String(1000), nullable=False,
        ),
        sa.Column(
            "base_amount", sa.Numeric(18, 2), nullable=False,
        ),
        sa.Column(
            "gross_amount", sa.Numeric(18, 2), nullable=False,
        ),
        sa.Column(
            "currency_code", sa.String(3),
            nullable=False, server_default="UAH",
        ),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "company_id", "id", name="uq_pts_company_id"
        ),
        sa.UniqueConstraint(
            "company_id", "request_key", name="uq_pts_request"
        ),
        sa.UniqueConstraint(
            "company_id", "termination_event_id", "revision",
            name="uq_pts_event_revision",
        ),
        sa.UniqueConstraint(
            "company_id", "supersedes_id",
            name="uq_pts_supersedes",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"],
            name="fk_pts_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "payroll_input_id"],
            ["payroll_inputs.company_id", "payroll_inputs.id"],
            name="fk_pts_input",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "employment_contract_id"],
            [
                "employment_contracts.company_id",
                "employment_contracts.id",
            ],
            name="fk_pts_contract",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "termination_event_id"],
            ["employment_events.company_id", "employment_events.id"],
            name="fk_pts_event",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "supersedes_id"],
            [
                "payroll_termination_severances.company_id",
                "payroll_termination_severances.id",
            ],
            name="fk_pts_supersedes",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"],
            name="fk_pts_created_by",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_pts_revision",
        ),
        sa.CheckConstraint(
            "(revision = 1 AND supersedes_id IS NULL) OR "
            "(revision > 1 AND supersedes_id IS NOT NULL)",
            name="ck_pts_revision_link",
        ),
        sa.CheckConstraint(
            "policy_category IN ('average_months','minimum_wages')",
            name="ck_pts_category",
        ),
        sa.CheckConstraint(
            "multiplier > 0",
            name="ck_pts_multiplier",
        ),
        sa.CheckConstraint(
            "base_amount > 0 AND "
            "base_amount <> 'NaN'::numeric AND "
            "gross_amount > 0 AND "
            "gross_amount <> 'NaN'::numeric",
            name="ck_pts_amounts",
        ),
        sa.CheckConstraint(
            "currency_code = 'UAH'",
            name="ck_pts_currency",
        ),
        sa.CheckConstraint(
            "length(trim(request_key)) > 0 AND "
            "length(trim(order_number)) > 0 AND "
            "length(trim(labour_code_provision)) > 0 AND "
            "length(trim(source_reference)) > 0 AND "
            "length(trim(basis_reference)) > 0",
            name="ck_pts_evidence",
        ),
    )

    op.execute("""
    CREATE FUNCTION payroll_severance_source_immutable()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        RAISE EXCEPTION
            'Historical severance source is immutable'
            USING ERRCODE = '23514';
    END;
    $$;
    """)

    op.execute("""
    CREATE TRIGGER trg_payroll_severance_immutable
    BEFORE UPDATE OR DELETE
    ON payroll_termination_severances
    FOR EACH ROW
    EXECUTE FUNCTION payroll_severance_source_immutable()
    """)


    op.execute("""
    CREATE TRIGGER trg_payroll_severance_no_truncate
    BEFORE TRUNCATE
    ON payroll_termination_severances
    FOR EACH STATEMENT
    EXECUTE FUNCTION payroll_severance_source_immutable()
    """)


def downgrade():
    # Refuse data-destroying downgrade.
    # The migration can be reversed only while its source
    # table is empty.
    op.execute("""
    LOCK TABLE payroll_termination_severances
    IN ACCESS EXCLUSIVE MODE
    """)

    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM payroll_termination_severances
        ) THEN
            RAISE EXCEPTION
                'Cannot drop documented severance history';
        END IF;
    END;
    $$;
    """)

    op.execute("""
    DROP TRIGGER trg_payroll_severance_no_truncate
    ON payroll_termination_severances
    """)

    op.execute("""
    DROP TRIGGER trg_payroll_severance_immutable
    ON payroll_termination_severances
    """)

    op.execute("""
    DROP FUNCTION payroll_severance_source_immutable()
    """)

    op.drop_table("payroll_termination_severances")
