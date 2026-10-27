"""Deferred reversal integrity for posted journal records.

Revision ID: 13d4a7b8c057
Revises: 13d4a7b8c056

A reversal header is inserted before its lines. Validation is
therefore deferred until the transaction is ready to commit.
"""

from alembic import op

revision = "13d4a7b8c057"
down_revision = "13d4a7b8c056"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION enforce_cash_reversal_integrity()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $body$
    DECLARE
        original journal_entries%ROWTYPE;
        reversal journal_entries%ROWTYPE;
        original_count bigint;
        reversal_count bigint;
        mismatches bigint;
    BEGIN
        SELECT * INTO reversal
        FROM journal_entries
        WHERE id = NEW.id;

        IF NOT FOUND OR reversal.reversal_of_id IS NULL THEN
            RETURN NULL;
        END IF;

        SELECT * INTO original
        FROM journal_entries
        WHERE id = reversal.reversal_of_id;

        IF NOT FOUND
           OR original.company_id IS DISTINCT FROM reversal.company_id
           OR original.status IS DISTINCT FROM 'reversed'
           OR original.reversal_of_id IS NOT NULL
           OR reversal.status IS DISTINCT FROM 'posted'
           OR reversal.entry_date < original.entry_date
           OR reversal.posted_at IS NULL
           OR original.reversed_at IS NULL
           OR original.reversed_by IS NULL
           OR reversal.payment_id IS DISTINCT FROM original.payment_id
           OR reversal.payroll_disbursement_id
              IS DISTINCT FROM original.payroll_disbursement_id
           OR reversal.document_id IS DISTINCT FROM original.document_id
        THEN
            RAISE EXCEPTION
                'Posted reversal has invalid original or source provenance'
                USING ERRCODE = '23514';
        END IF;

        SELECT COUNT(*) INTO original_count
        FROM journal_entry_lines
        WHERE journal_entry_id = original.id;

        SELECT COUNT(*) INTO reversal_count
        FROM journal_entry_lines
        WHERE journal_entry_id = reversal.id;

        IF original_count < 2
           OR original_count IS DISTINCT FROM reversal_count
        THEN
            RAISE EXCEPTION
                'Posted reversal line cardinality mismatch'
                USING ERRCODE = '23514';
        END IF;

        SELECT COUNT(*) INTO mismatches
        FROM journal_entry_lines a
        FULL JOIN journal_entry_lines b
          ON b.journal_entry_id = reversal.id
         AND a.journal_entry_id = original.id
         AND b.line_no = a.line_no
        WHERE (a.journal_entry_id = original.id
               OR b.journal_entry_id = reversal.id)
          AND (
              a.id IS NULL
              OR b.id IS NULL
              OR a.account_id IS DISTINCT FROM b.account_id
              OR a.debit IS DISTINCT FROM b.credit
              OR a.credit IS DISTINCT FROM b.debit
          );

        IF mismatches <> 0 THEN
            RAISE EXCEPTION
                'Posted reversal must exactly invert original GL lines'
                USING ERRCODE = '23514';
        END IF;

        RETURN NULL;
    END;
    $body$;
    """)

    op.execute("""
    CREATE CONSTRAINT TRIGGER trg_cash_reversal_integrity
    AFTER INSERT OR UPDATE ON journal_entries
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    WHEN (NEW.reversal_of_id IS NOT NULL)
    EXECUTE FUNCTION enforce_cash_reversal_integrity();
    """)


def downgrade():
    op.execute("""
        DROP TRIGGER trg_cash_reversal_integrity
        ON journal_entries
    """)
    op.execute("""
        DROP FUNCTION enforce_cash_reversal_integrity()
    """)
