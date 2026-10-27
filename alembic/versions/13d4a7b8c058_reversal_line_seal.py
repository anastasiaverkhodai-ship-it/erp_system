"""Seal reversal journal lines at the transaction boundary.

Revision ID: 13d4a7b8c058
Revises: 13d4a7b8c057
"""

from alembic import op

revision = "13d4a7b8c058"
down_revision = "13d4a7b8c057"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION enforce_reversal_line_seal()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $body$
    DECLARE
        original_id integer;
        current_status text;
        source_count bigint;
        reversal_count bigint;
        matched_count bigint;
    BEGIN
        SELECT j.reversal_of_id, j.status
        INTO original_id, current_status
        FROM journal_entries j
        WHERE j.id = NEW.journal_entry_id;

        IF NOT FOUND
           OR original_id IS NULL
           OR current_status <> 'posted'
        THEN
            RETURN NULL;
        END IF;

        SELECT COUNT(*) INTO source_count
        FROM journal_entry_lines
        WHERE journal_entry_id = original_id;

        SELECT COUNT(*) INTO reversal_count
        FROM journal_entry_lines
        WHERE journal_entry_id = NEW.journal_entry_id;

        SELECT COUNT(*) INTO matched_count
        FROM journal_entry_lines r
        JOIN journal_entry_lines s
          ON s.journal_entry_id = original_id
         AND s.line_no = r.line_no
         AND s.account_id = r.account_id
         AND s.debit = r.credit
         AND s.credit = r.debit
        WHERE r.journal_entry_id = NEW.journal_entry_id;

        IF source_count < 2
           OR reversal_count <> source_count
           OR matched_count <> source_count
        THEN
            RAISE EXCEPTION
                'Posted reversal lines must exactly invert source'
                USING ERRCODE = '23514';
        END IF;

        RETURN NULL;
    END;
    $body$;
    """)

    op.execute("""
    CREATE CONSTRAINT TRIGGER trg_reversal_line_seal
    AFTER INSERT ON journal_entry_lines
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION enforce_reversal_line_seal();
    """)


def downgrade():
    op.execute("""
    DROP TRIGGER trg_reversal_line_seal
    ON journal_entry_lines
    """)
    op.execute("""
    DROP FUNCTION enforce_reversal_line_seal()
    """)
