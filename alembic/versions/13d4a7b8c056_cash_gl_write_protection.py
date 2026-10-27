"""Protect posted journal creation and immutable GL lines.

Revision ID: 13d4a7b8c056
Revises: 13d4a7b8c055

Narrow protection for all companies.
No changes to historic accounting data.
"""

from alembic import op

revision = "13d4a7b8c056"
down_revision = "13d4a7b8c055"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION enforce_posted_journal_insert()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $body$
    DECLARE
        original_company integer;
        original_status text;
        original_date date;
    BEGIN
        IF NEW.status <> 'posted' THEN
            RETURN NEW;
        END IF;

        -- Normal journals must begin as DRAFT and use the
        -- guarded accounting posting lifecycle.
        IF NEW.reversal_of_id IS NULL THEN
            RAISE EXCEPTION
                'Direct posted original journal insert prohibited'
                USING ERRCODE = '23514';
        END IF;

        -- A reversal may be inserted already POSTED, as existing
        -- accounting_reversal does. Validate its original identity.
        SELECT j.company_id, j.status, j.entry_date
        INTO original_company, original_status, original_date
        FROM journal_entries j
        WHERE j.id = NEW.reversal_of_id
        FOR UPDATE;

        IF NOT FOUND
            OR original_company IS DISTINCT FROM NEW.company_id
            OR original_status NOT IN ('posted', 'reversed')
            OR NEW.entry_date < original_date
            OR NEW.id = NEW.reversal_of_id
            OR NEW.posted_at IS NULL
        THEN
            RAISE EXCEPTION
                'Invalid posted reversal journal identity'
                USING ERRCODE = '23514';
        END IF;

        RETURN NEW;
    END;
    $body$;
    """)

    op.execute("""
    CREATE TRIGGER trg_posted_journal_insert_guard
    BEFORE INSERT ON journal_entries
    FOR EACH ROW
    EXECUTE FUNCTION enforce_posted_journal_insert();
    """)

    op.execute("""
    CREATE FUNCTION protect_finalized_journal_lines()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $body$
    DECLARE
        parent_status text;
        parent_reversal_id integer;
        parent_id integer;
    BEGIN
        IF TG_OP = 'INSERT' THEN
            parent_id := NEW.journal_entry_id;
        ELSE
            parent_id := OLD.journal_entry_id;
        END IF;

        SELECT j.status, j.reversal_of_id
        INTO parent_status, parent_reversal_id
        FROM journal_entries j
        WHERE j.id = parent_id
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'Journal line parent missing'
                USING ERRCODE = '23503';
        END IF;

        IF TG_OP = 'INSERT' THEN
            -- Existing reversal service inserts the POSTED
            -- reversal header before inserting its lines.
            -- This permits that existing atomic creation path.
            IF parent_status = 'draft'
                OR (
                    parent_status = 'posted'
                    AND parent_reversal_id IS NOT NULL
                )
            THEN
                RETURN NEW;
            END IF;

            RAISE EXCEPTION
                'Cannot append lines to finalized original journal'
                USING ERRCODE = '23514';
        END IF;

        IF parent_status <> 'draft' THEN
            RAISE EXCEPTION
                'Finalized journal lines are immutable'
                USING ERRCODE = '23514';
        END IF;

        IF TG_OP = 'UPDATE' THEN
            -- Even while DRAFT, cross-entry row movement is
            -- prohibited by this database contract.
            IF NEW.journal_entry_id IS DISTINCT FROM OLD.journal_entry_id
            THEN
                RAISE EXCEPTION
                    'Moving journal lines between entries prohibited'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END IF;

        RETURN OLD;
    END;
    $body$;
    """)

    op.execute("""
    CREATE TRIGGER trg_finalized_journal_lines_guard
    BEFORE INSERT OR UPDATE OR DELETE
    ON journal_entry_lines
    FOR EACH ROW
    EXECUTE FUNCTION protect_finalized_journal_lines();
    """)


def downgrade():
    op.execute("""
        DROP TRIGGER trg_finalized_journal_lines_guard
        ON journal_entry_lines
    """)
    op.execute("""
        DROP FUNCTION protect_finalized_journal_lines()
    """)
    op.execute("""
        DROP TRIGGER trg_posted_journal_insert_guard
        ON journal_entries
    """)
    op.execute("""
        DROP FUNCTION enforce_posted_journal_insert()
    """)
