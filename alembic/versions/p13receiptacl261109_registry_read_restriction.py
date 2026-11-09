"""Restrict application receipt reads to the RETURNING id column.

Revision ID: p13receiptacl261109
Revises: p13receiptlife261109

This is a fail-closed boundary, not tenant authorization.
There is no general tenant-scoped receipt reader yet.
"""

from alembic import op

revision = "p13receiptacl261109"
down_revision = "p13receiptlife261109"
branch_labels = None
depends_on = None


def upgrade():
    # Remove unrestricted access to financial receipt fields.
    op.execute("""
        REVOKE SELECT ON TABLE
            public.payroll_external_receipt_registry
        FROM PUBLIC, erp_user
    """)

    # Existing registration uses INSERT ... RETURNING id.
    # Only the generated id column remains readable.
    op.execute("""
        GRANT SELECT (id) ON TABLE
            public.payroll_external_receipt_registry
        TO erp_user
    """)

    # Lifecycle data must not be exposed until a trusted
    # user-to-company authorization contract is implemented.
    op.execute("""
        REVOKE ALL ON TABLE
            public.payroll_external_receipt_lifecycle
        FROM PUBLIC, erp_user
    """)


def downgrade():
    raise RuntimeError(
        "Receipt security restrictions require "
        "a separately reviewed recovery migration"
    )
