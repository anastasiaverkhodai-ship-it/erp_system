"""Phase 13 C4-B2 technical remittance schema.

Revision ID: 13d4a7b8c024
Revises: 13d4a7b8c023

Storage only — no money movement or payroll posting.
"""

from alembic import op
import sqlalchemy as sa


revision = "13d4a7b8c024"
down_revision = "13d4a7b8c023"
branch_labels = None
depends_on = None


def upgrade():

    op.create_table(
        "payroll_deduction_remittance_plans",

        sa.Column(
            "id", sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "company_id", sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "instruction_id", sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "beneficiary_reference",
            sa.String(120),
            nullable=False,
        ),
        sa.Column(
            "currency_code",
            sa.String(3),
            nullable=False,
        ),
        sa.Column(
            "available_amount",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),

        sa.ForeignKeyConstraint(
            ["company_id", "instruction_id"],
            [
                "payroll_deduction_instructions.company_id",
                "payroll_deduction_instructions.id",
            ],
            name="fk_pdrp_company_instruction",
        ),
        sa.UniqueConstraint(
            "company_id",
            "instruction_id",
            name="uq_pdrp_company_instruction",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_pdrp_company_id_id",
        ),
        sa.CheckConstraint(
            "available_amount >= 0",
            name="ck_pdrp_available_nonnegative",
        ),
        sa.CheckConstraint(
            "currency_code = upper(currency_code) "
            "AND char_length(currency_code) = 3",
            name="ck_pdrp_currency",
        ),
        sa.CheckConstraint(
            "char_length(trim(beneficiary_reference)) > 0 "
            "AND beneficiary_reference = trim(beneficiary_reference)",
            name="ck_pdrp_beneficiary",
        ),
    )

    op.create_index(
        "ix_pdrp_company_beneficiary",
        "payroll_deduction_remittance_plans",
        ["company_id", "beneficiary_reference"],
    )

    op.create_table(
        "payroll_deduction_remittance_events",

        sa.Column(
            "id", sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "company_id", sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "plan_id", sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "event_id", sa.String(100),
            nullable=False,
        ),
        sa.Column(
            "amount",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),

        sa.ForeignKeyConstraint(
            ["company_id", "plan_id"],
            [
                "payroll_deduction_remittance_plans.company_id",
                "payroll_deduction_remittance_plans.id",
            ],
            name="fk_pdrevent_company_plan",
        ),
        sa.UniqueConstraint(
            "company_id",
            "event_id",
            name="uq_pdrevent_company_event",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_pdrevent_company_id_id",
        ),
        sa.CheckConstraint(
            "amount > 0",
            name="ck_pdrevent_positive_amount",
        ),
        sa.CheckConstraint(
            "char_length(trim(event_id)) > 0 "
            "AND event_id = trim(event_id)",
            name="ck_pdrevent_event_id",
        ),
    )

    op.create_index(
        "ix_pdrevent_company_plan_created",
        "payroll_deduction_remittance_events",
        ["company_id", "plan_id", "created_at", "id"],
    )

    # PostgreSQL append-only protection.
    #
    # Both opening plans and event history are immutable.
    # Application-level checks remain necessary for
    # allocation ceilings and beneficiary reconciliation.

    op.execute("""
        CREATE FUNCTION pdrm_reject_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'Payroll deduction remittance records are immutable';
        END;
        $$;
    """)

    op.execute("""
        CREATE TRIGGER trg_pdrp_immutable
        BEFORE UPDATE OR DELETE
        ON payroll_deduction_remittance_plans
        FOR EACH ROW
        EXECUTE FUNCTION pdrm_reject_mutation();
    """)

    op.execute("""
        CREATE TRIGGER trg_pdrevent_immutable
        BEFORE UPDATE OR DELETE
        ON payroll_deduction_remittance_events
        FOR EACH ROW
        EXECUTE FUNCTION pdrm_reject_mutation();
    """)


def downgrade():

    op.execute("""
        DROP TRIGGER trg_pdrevent_immutable
        ON payroll_deduction_remittance_events;
    """)

    op.execute("""
        DROP TRIGGER trg_pdrp_immutable
        ON payroll_deduction_remittance_plans;
    """)

    op.execute("""
        DROP FUNCTION pdrm_reject_mutation();
    """)

    op.drop_index(
        "ix_pdrevent_company_plan_created",
        table_name="payroll_deduction_remittance_events",
    )

    op.drop_table(
        "payroll_deduction_remittance_events",
    )

    op.drop_index(
        "ix_pdrp_company_beneficiary",
        table_name="payroll_deduction_remittance_plans",
    )

    op.drop_table(
        "payroll_deduction_remittance_plans",
    )
