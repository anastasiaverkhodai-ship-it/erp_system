"""Protect payroll deduction remittance history from TRUNCATE.

Revision ID: 13d4a7b8c025
Revises: 13d4a7b8c024

This migration adds statement-level TRUNCATE protection.
Existing UPDATE/DELETE immutability remains unchanged.
"""

from alembic import op
import sqlalchemy as sa


revision = "13d4a7b8c025"
down_revision = "13d4a7b8c024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL executes this DDL transactionally.
    # Reuse the existing fail-closed trigger function
    # introduced by revision 024.
    op.execute("""
        CREATE TRIGGER trg_pdrp_no_truncate
        BEFORE TRUNCATE
        ON payroll_deduction_remittance_plans
        FOR EACH STATEMENT
        EXECUTE FUNCTION pdrm_reject_mutation()
    """)

    op.execute("""
        CREATE TRIGGER trg_pdrevent_no_truncate
        BEFORE TRUNCATE
        ON payroll_deduction_remittance_events
        FOR EACH STATEMENT
        EXECUTE FUNCTION pdrm_reject_mutation()
    """)


def downgrade() -> None:
    # Do not remove safeguards when any history exists.
    # Exclusive locks prevent races with concurrent inserts.
    op.execute(
        "LOCK TABLE payroll_deduction_remittance_events "
        "IN ACCESS EXCLUSIVE MODE"
    )

    op.execute(
        "LOCK TABLE payroll_deduction_remittance_plans "
        "IN ACCESS EXCLUSIVE MODE"
    )

    has_history = op.get_bind().execute(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM payroll_deduction_remittance_events"
            ") OR EXISTS ("
            "SELECT 1 FROM payroll_deduction_remittance_plans"
            ")"
        )
    ).scalar_one()

    if has_history:
        raise RuntimeError(
            "Cannot remove remittance TRUNCATE protection "
            "while financial history exists"
        )

    op.execute("""
        DROP TRIGGER trg_pdrevent_no_truncate
        ON payroll_deduction_remittance_events
    """)

    op.execute("""
        DROP TRIGGER trg_pdrp_no_truncate
        ON payroll_deduction_remittance_plans
    """)
