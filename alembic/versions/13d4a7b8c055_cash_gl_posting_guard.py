"""Reject unauthorised physical cash GL status transitions.

Revision ID: 13d4a7b8c055
Revises: 13d4a7b8c054

Scope: DRAFT -> POSTED updates only.
INSERT and journal-line mutation protection remain separate gates.
"""

from alembic import op

revision = "13d4a7b8c055"
down_revision = "13d4a7b8c054"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION enforce_cash_gl_status_transition()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $body$
    DECLARE
        cash_account integer;
        cash_count integer;
        cash_debit numeric;
        cash_credit numeric;
        source_amount numeric;
        source_direction text;
    BEGIN
        IF NOT (
            OLD.status = 'draft'
            AND NEW.status = 'posted'
        ) THEN
            RETURN NEW;
        END IF;

        -- Cash identity is established from the actual CashDesk
        -- and immutable historical source account snapshots.
        SELECT COUNT(*)
        INTO cash_count
        FROM journal_entry_lines l
        WHERE l.journal_entry_id = NEW.id
          AND EXISTS (
              SELECT 1 FROM cash_desks d
              WHERE d.company_id = NEW.company_id
                AND d.accounting_account_id = l.account_id

              UNION ALL

              SELECT 1 FROM payments p
              WHERE p.company_id = NEW.company_id
                AND p.cash_desk_id IS NOT NULL
                AND p.cash_account_id_snapshot = l.account_id

              UNION ALL

              SELECT 1 FROM payroll_disbursements pd
              WHERE pd.company_id = NEW.company_id
                AND pd.cash_desk_id IS NOT NULL
                AND pd.cash_account_id_snapshot = l.account_id
          );

        IF cash_count = 0 THEN
            RETURN NEW;
        END IF;

        IF cash_count <> 1 THEN
            RAISE EXCEPTION
                'Physical cash journal must have one cash GL leg'
                USING ERRCODE = '23514';
        END IF;

        IF num_nonnulls(
            NEW.payment_id,
            NEW.payroll_disbursement_id
        ) <> 1 THEN
            RAISE EXCEPTION
                'Physical cash posting requires Payment or Payroll'
                USING ERRCODE = '23514';
        END IF;

        IF NEW.payment_id IS NOT NULL THEN
            SELECT
                p.cash_account_id_snapshot,
                p.amount,
                p.direction
            INTO
                cash_account,
                source_amount,
                source_direction
            FROM payments p
            JOIN cash_desks d
              ON d.company_id = p.company_id
             AND d.id = p.cash_desk_id
            WHERE p.company_id = NEW.company_id
              AND p.id = NEW.payment_id
              AND p.status = 'confirmed'
              AND p.bank_account_id IS NULL
              AND p.cash_desk_id IS NOT NULL
              AND p.cash_account_id_snapshot IS NOT NULL
              AND p.currency_code = 'UAH'
              AND d.currency_code = p.currency_code
              AND NEW.entry_date = p.payment_date;

            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'Cash Payment posting provenance invalid'
                    USING ERRCODE = '23514';
            END IF;

        ELSE
            SELECT
                pd.cash_account_id_snapshot,
                pd.amount,
                'outgoing'
            INTO
                cash_account,
                source_amount,
                source_direction
            FROM payroll_disbursements pd
            JOIN cash_desks d
              ON d.company_id = pd.company_id
             AND d.id = pd.cash_desk_id
            WHERE pd.company_id = NEW.company_id
              AND pd.id = NEW.payroll_disbursement_id
              AND pd.bank_account_id IS NULL
              AND pd.cash_desk_id IS NOT NULL
              AND pd.cash_account_id_snapshot IS NOT NULL
              AND pd.currency_code = 'UAH'
              AND d.currency_code = pd.currency_code
              AND NEW.entry_date = pd.payment_date;

            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'Cash Payroll posting provenance invalid'
                    USING ERRCODE = '23514';
            END IF;
        END IF;

        SELECT
            COUNT(*),
            COALESCE(SUM(l.debit), 0),
            COALESCE(SUM(l.credit), 0)
        INTO cash_count, cash_debit, cash_credit
        FROM journal_entry_lines l
        WHERE l.journal_entry_id = NEW.id
          AND l.account_id = cash_account;

        IF cash_count <> 1
           OR source_amount IS NULL
           OR source_amount <= 0
           OR (
               source_direction = 'incoming'
               AND (
                   cash_debit <> source_amount
                   OR cash_credit <> 0
               )
           )
           OR (
               source_direction = 'outgoing'
               AND (
                   cash_credit <> source_amount
                   OR cash_debit <> 0
               )
           )
           OR source_direction NOT IN ('incoming', 'outgoing')
        THEN
            RAISE EXCEPTION
                'Physical cash GL amount/direction mismatch'
                USING ERRCODE = '23514';
        END IF;

        RETURN NEW;
    END;
    $body$;
    """)

    op.execute("""
    CREATE TRIGGER trg_cash_gl_status_transition
    BEFORE UPDATE OF status, posted_at
    ON journal_entries
    FOR EACH ROW
    EXECUTE FUNCTION enforce_cash_gl_status_transition();
    """)


def downgrade():
    op.execute("""
        DROP TRIGGER trg_cash_gl_status_transition
        ON journal_entries
    """)
    op.execute("""
        DROP FUNCTION enforce_cash_gl_status_transition()
    """)
