"""Allow protected receipt validation to lock source rows.

Revision ID: p13receiptlock261109
Revises: p13receiptacl261109

The application role receives no source SELECT or UPDATE.
The protected non-login role alone receives narrow privileges
needed by the existing SELECT ... FOR SHARE validation.
"""

from alembic import op

revision = "p13receiptlock261109"
down_revision = "p13receiptacl261109"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        GRANT SELECT ON TABLE
            public.payroll_payment_tax_evidence,
            public.payroll_payment_tax_allocation_candidates,
            public.payroll_disbursements,
            public.payroll_payment_tax_allocation_corrections,
            public.payroll_payment_tax_allocation_candidate_events
        TO erp_snapshot_owner
    """)

    # PostgreSQL row-lock clauses require UPDATE privilege.
    # Restrict it to id columns on precisely the three
    # tables locked by FOR SHARE OF e, c, p.
    op.execute("""
        GRANT UPDATE (id) ON TABLE
            public.payroll_payment_tax_evidence,
            public.payroll_payment_tax_allocation_candidates,
            public.payroll_disbursements
        TO erp_snapshot_owner
    """)

    op.execute("""
        ALTER FUNCTION public.p13_receipt_check_evidence()
        SECURITY DEFINER
    """)

    op.execute("""
        ALTER FUNCTION public.p13_receipt_check_evidence()
        SET search_path = pg_catalog, public
    """)

    # Trigger execution does not require callers to have
    # direct EXECUTE permission on the trigger function.
    op.execute("""
        REVOKE EXECUTE ON FUNCTION
            public.p13_receipt_check_evidence()
        FROM PUBLIC, erp_user
    """)


def downgrade():
    raise RuntimeError(
        "Protected receipt security requires controlled "
        "recovery rather than automatic downgrade"
    )
