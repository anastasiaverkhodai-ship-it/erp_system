"""Derive review-only external receipt lifecycle status.

Revision ID: p13receiptlife261109
Revises: p13receiptbind261108

A current source does not establish actual withholding,
issuer approval or statutory report eligibility.
"""

from alembic import op

revision = "p13receiptlife261109"
down_revision = "p13receiptbind261108"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE VIEW public.payroll_external_receipt_lifecycle
        WITH (security_barrier = true)
        AS
        SELECT
            r.id AS registry_id,
            r.company_id,
            r.payroll_disbursement_id,
            r.evidence_id,
            r.issuer_id,
            r.receipt_id,
            r.created_at,
            lifecycle.lifecycle_status,
            (
                lifecycle.lifecycle_status = 'SOURCE_CURRENT'
            ) AS source_current,
            'NOT_VERIFIED'::text AS actual_withholding,
            'NOT_VERIFIED'::text AS issuer_identity,
            'BLOCKED'::text AS official_generation
        FROM public.payroll_external_receipt_registry r

        LEFT JOIN public.payroll_payment_tax_evidence e
          ON e.company_id = r.company_id
         AND e.id = r.evidence_id
         AND e.payroll_disbursement_id =
             r.payroll_disbursement_id

        LEFT JOIN public.payroll_payment_tax_allocation_candidates c
          ON c.company_id = e.company_id
         AND c.id = e.candidate_id

        LEFT JOIN public.payroll_disbursements p
          ON p.company_id = r.company_id
         AND p.id = r.payroll_disbursement_id

        LEFT JOIN LATERAL (
            SELECT MAX(x.id) AS newest_id
            FROM public.payroll_payment_tax_allocation_corrections x
            WHERE x.company_id = e.company_id
              AND x.original_candidate_id = e.candidate_id
        ) corrections ON true

        LEFT JOIN LATERAL (
            SELECT EXISTS (
                SELECT 1
                FROM public.payroll_payment_tax_allocation_candidate_events v
                WHERE v.company_id = e.company_id
                  AND v.candidate_id = e.candidate_id
                  AND v.event_kind = 'revoked'
            ) AS revoked
        ) events ON true

        CROSS JOIN LATERAL (
            SELECT CASE
                WHEN e.id IS NULL OR c.id IS NULL OR p.id IS NULL
                  OR p.payroll_calculation_id IS DISTINCT FROM
                     c.payroll_calculation_id
                  THEN 'SOURCE_MISSING'

                WHEN p.confirmed_at IS NULL
                  OR p.cancelled_at IS NOT NULL
                  OR p.reversed_on IS NOT NULL
                  OR p.currency_code IS DISTINCT FROM 'UAH'
                  THEN 'PAYMENT_INACTIVE'

                WHEN r.amount IS DISTINCT FROM e.amount
                  OR r.component IS DISTINCT FROM c.component
                  OR r.payment_date IS DISTINCT FROM e.payment_date
                  OR r.payment_date IS DISTINCT FROM p.payment_date
                  THEN 'SOURCE_MISMATCH'

                WHEN e.correction_id IS DISTINCT FROM
                     corrections.newest_id
                  THEN 'CORRECTION_SUPERSEDED'

                WHEN COALESCE(events.revoked, false)
                  AND corrections.newest_id IS NULL
                  THEN 'SOURCE_REVOKED'

                ELSE 'SOURCE_CURRENT'
            END AS lifecycle_status
        ) lifecycle;
    """)

    # Internal reviewer access only. Application authorization
    # requires a separate tenant-scoped security design.
    op.execute("""
        REVOKE ALL ON
            public.payroll_external_receipt_lifecycle
        FROM PUBLIC, erp_user
    """)

    op.execute("""
        COMMENT ON VIEW
            public.payroll_external_receipt_lifecycle
        IS 'Read-only source currency; NOT financial approval.
            Issuer and actual withholding remain unverified.
            Official statutory generation remains blocked.'
    """)


def downgrade():
    raise RuntimeError(
        "Receipt lifecycle review requires controlled "
        "recovery rather than automatic downgrade"
    )
