"""Record reviewer and decision time for rejected tax evidence.

Revision ID: 13d4a7b8c013
Revises: 13d4a7b8c012
"""

from alembic import op
import sqlalchemy as sa


revision = "13d4a7b8c013"
down_revision = "13d4a7b8c012"
branch_labels = None
depends_on = None


NEW_CHECK = (
    "(verification_status = 'pending' "
    "AND verified_by IS NULL "
    "AND verified_at IS NULL) "
    "OR (verification_status IN ('verified', 'rejected') "
    "AND verified_by IS NOT NULL "
    "AND verified_at IS NOT NULL)"
)

OLD_CHECK = (
    "(verification_status = 'verified' "
    "AND verified_by IS NOT NULL "
    "AND verified_at IS NOT NULL) "
    "OR (verification_status <> 'verified' "
    "AND verified_by IS NULL "
    "AND verified_at IS NULL)"
)


def upgrade() -> None:
    # Existing rejected records have no reviewer information.
    # Refuse migration rather than invent historical audit data.
    bind = op.get_bind()

    missing = bind.execute(
        sa.text("""
            SELECT COUNT(*)
            FROM payroll_employee_tax_evidence
            WHERE verification_status = 'rejected'
              AND (
                  verified_by IS NULL
                  OR verified_at IS NULL
              )
        """)
    ).scalar_one()

    if missing:
        raise RuntimeError(
            "Cannot migrate rejected evidence without "
            "historical reviewer information"
        )

    op.drop_constraint(
        "ck_payroll_tax_evidence_verification",
        "payroll_employee_tax_evidence",
        type_="check",
    )

    op.create_check_constraint(
        "ck_payroll_tax_evidence_verification",
        "payroll_employee_tax_evidence",
        NEW_CHECK,
    )


def downgrade() -> None:
    # Old schema cannot represent rejected decisions with reviewers.
    bind = op.get_bind()

    reviewed_rejections = bind.execute(
        sa.text("""
            SELECT COUNT(*)
            FROM payroll_employee_tax_evidence
            WHERE verification_status = 'rejected'
              AND (
                  verified_by IS NOT NULL
                  OR verified_at IS NOT NULL
              )
        """)
    ).scalar_one()

    if reviewed_rejections:
        raise RuntimeError(
            "Cannot downgrade: rejected evidence has "
            "review audit data that the old schema cannot retain"
        )

    op.drop_constraint(
        "ck_payroll_tax_evidence_verification",
        "payroll_employee_tax_evidence",
        type_="check",
    )

    op.create_check_constraint(
        "ck_payroll_tax_evidence_verification",
        "payroll_employee_tax_evidence",
        OLD_CHECK,
    )
