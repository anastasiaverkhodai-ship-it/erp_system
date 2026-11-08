"""Bind external receipts to effective payroll evidence at insertion.

Revision ID: p13receiptbind261108
Revises: p13receipt261108

This checks internal database consistency, not independent
issuer identity or actual tax withholding.
"""

from alembic import op

revision = "p13receiptbind261108"
down_revision = "p13receipt261108"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE FUNCTION public.p13_receipt_check_evidence()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            source_record record;
            latest_correction integer;
            revoked boolean;
        BEGIN
            SELECT
                e.amount AS evidence_amount,
                e.payment_date AS evidence_payment_date,
                e.correction_id,
                e.candidate_id,
                c.component AS evidence_component,
                p.payment_date AS payment_date,
                p.confirmed_at,
                p.cancelled_at,
                p.reversed_on,
                p.currency_code
            INTO source_record
            FROM public.payroll_payment_tax_evidence e
            JOIN public.payroll_payment_tax_allocation_candidates c
              ON c.company_id = e.company_id
             AND c.id = e.candidate_id
            JOIN public.payroll_disbursements p
              ON p.company_id = e.company_id
             AND p.id = e.payroll_disbursement_id
             AND p.payroll_calculation_id =
                 c.payroll_calculation_id
            WHERE e.company_id = NEW.company_id
              AND e.id = NEW.evidence_id
              AND e.payroll_disbursement_id =
                  NEW.payroll_disbursement_id
            FOR SHARE OF e, c, p;

            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'Receipt evidence/payment source mismatch'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.amount IS DISTINCT FROM
                    source_record.evidence_amount
                OR NEW.component IS DISTINCT FROM
                    source_record.evidence_component
                OR NEW.payment_date IS DISTINCT FROM
                    source_record.evidence_payment_date
                OR NEW.payment_date IS DISTINCT FROM
                    source_record.payment_date
            THEN
                RAISE EXCEPTION
                    'Receipt amount/component/date mismatch'
                    USING ERRCODE = '23514';
            END IF;

            IF source_record.confirmed_at IS NULL
               OR source_record.cancelled_at IS NOT NULL
               OR source_record.reversed_on IS NOT NULL
               OR source_record.currency_code <> 'UAH'
            THEN
                RAISE EXCEPTION
                    'Receipt references inactive payroll payment'
                    USING ERRCODE = '23514';
            END IF;

            SELECT MAX(id)
            INTO latest_correction
            FROM public.payroll_payment_tax_allocation_corrections
            WHERE company_id = NEW.company_id
              AND original_candidate_id =
                  source_record.candidate_id;

            IF source_record.correction_id
                IS DISTINCT FROM latest_correction
            THEN
                RAISE EXCEPTION
                    'Receipt references stale correction'
                    USING ERRCODE = '23514';
            END IF;

            SELECT EXISTS (
                SELECT 1
                FROM public.payroll_payment_tax_allocation_candidate_events
                WHERE company_id = NEW.company_id
                  AND candidate_id =
                      source_record.candidate_id
                  AND event_kind = 'revoked'
            )
            INTO revoked;

            IF revoked AND latest_correction IS NULL THEN
                RAISE EXCEPTION
                    'Receipt references revoked candidate'
                    USING ERRCODE = '23514';
            END IF;

            RETURN NEW;
        END;
        $$;
    """)

    op.execute("""
        CREATE TRIGGER trg_p13_receipt_evidence_consistency
        BEFORE INSERT
        ON public.payroll_external_receipt_registry
        FOR EACH ROW
        EXECUTE FUNCTION
            public.p13_receipt_check_evidence();
    """)

    op.execute("""
        ALTER FUNCTION
            public.p13_receipt_check_evidence()
        OWNER TO erp_snapshot_owner
    """)

    op.execute("""
        REVOKE ALL ON FUNCTION
            public.p13_receipt_check_evidence()
        FROM PUBLIC
    """)


def downgrade():
    raise RuntimeError(
        "Receipt consistency enforcement requires "
        "a separately reviewed recovery migration"
    )
