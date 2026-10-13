"""Add immutable technical payroll deduction remainder ledger.

Revision ID: 13d4a7b8c023
Revises: 13d4a7b8c022

Production payroll posting remains disabled.
"""

from alembic import op
import sqlalchemy as sa


revision = "13d4a7b8c023"
down_revision = "13d4a7b8c022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payroll_deduction_remainders",
        sa.Column(
            "id",
            sa.Integer(),
            sa.Identity(),
            primary_key=True,
        ),
        sa.Column(
            "company_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "instruction_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "original_amount",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "currency_code",
            sa.String(3),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey(
                "users.id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "instruction_id"],
            [
                "payroll_deduction_instructions.company_id",
                "payroll_deduction_instructions.id",
            ],
            name="fk_pdr_company_instruction",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id",
            "instruction_id",
            name="uq_pdr_company_instruction",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_pdr_company_id_id",
        ),
        sa.CheckConstraint(
            "original_amount >= 0",
            name="ck_pdr_original_nonnegative",
        ),
        sa.CheckConstraint(
            "currency_code = upper(currency_code) "
            "AND char_length(currency_code) = 3",
            name="ck_pdr_currency",
        ),
    )

    op.create_table(
        "payroll_deduction_remainder_events",
        sa.Column(
            "id",
            sa.Integer(),
            sa.Identity(),
            primary_key=True,
        ),
        sa.Column(
            "company_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "remainder_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "event_id",
            sa.String(100),
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
            sa.ForeignKey(
                "users.id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "remainder_id"],
            [
                "payroll_deduction_remainders.company_id",
                "payroll_deduction_remainders.id",
            ],
            name="fk_pdre_company_remainder",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id",
            "event_id",
            name="uq_pdre_company_event",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_pdre_company_id_id",
        ),
        sa.CheckConstraint(
            "amount > 0",
            name="ck_pdre_positive_amount",
        ),
        sa.CheckConstraint(
            "char_length(trim(event_id)) > 0 "
            "AND event_id = trim(event_id)",
            name="ck_pdre_event_id",
        ),
    )

    op.create_index(
        "ix_pdre_company_remainder_created",
        "payroll_deduction_remainder_events",
        [
            "company_id",
            "remainder_id",
            "created_at",
            "id",
        ],
    )

    # Protect technical source and event history.
    # This function is intentionally fail-closed.
    op.execute(
        """
        CREATE FUNCTION pdr_prevent_history_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'Payroll deduction remainder history is append-only';
        END;
        $$
        """
    )

    for table, suffix in (
        ("payroll_deduction_remainders", "pdr"),
        ("payroll_deduction_remainder_events", "pdre"),
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{suffix}_no_update_delete
            BEFORE UPDATE OR DELETE
            ON {table}
            FOR EACH ROW
            EXECUTE FUNCTION pdr_prevent_history_mutation()
            """
        )

        op.execute(
            f"""
            CREATE TRIGGER trg_{suffix}_no_truncate
            BEFORE TRUNCATE
            ON {table}
            FOR EACH STATEMENT
            EXECUTE FUNCTION pdr_prevent_history_mutation()
            """
        )


def downgrade() -> None:

    # Fail closed before removing financial history.
    op.execute(
        "LOCK TABLE payroll_deduction_remainder_events "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        "LOCK TABLE payroll_deduction_remainders "
        "IN ACCESS EXCLUSIVE MODE"
    )

    has_history = op.get_bind().execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM payroll_deduction_remainder_events) OR EXISTS (SELECT 1 FROM payroll_deduction_remainders)")
    ).scalar_one()

    if has_history:
        raise RuntimeError(
            "Cannot downgrade payroll deduction remainder ledger: "
            "financial history exists"
        )

    for table, suffix in (
        ("payroll_deduction_remainder_events", "pdre"),
        ("payroll_deduction_remainders", "pdr"),
    ):
        op.execute(
            f"DROP TRIGGER trg_{suffix}_no_update_delete "
            f"ON {table}"
        )

        op.execute(
            f"DROP TRIGGER trg_{suffix}_no_truncate "
            f"ON {table}"
        )

    op.execute(
        "DROP FUNCTION pdr_prevent_history_mutation()"
    )

    op.drop_index(
        "ix_pdre_company_remainder_created",
        table_name="payroll_deduction_remainder_events",
    )

    op.drop_table(
        "payroll_deduction_remainder_events"
    )

    op.drop_table(
        "payroll_deduction_remainders"
    )
