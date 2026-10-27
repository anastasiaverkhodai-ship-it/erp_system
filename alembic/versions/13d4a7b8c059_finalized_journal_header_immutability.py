"""Enforce immutable finalized journal headers.

Revision ID: 13d4a7b8c059
Revises: 13d4a7b8c058

Allow the existing POSTED -> REVERSED accounting lifecycle.
Reject direct modification of finalized financial identity.
Does not change or backfill any historical data.
"""

from alembic import op

revision = "13d4a7b8c059"
down_revision = "13d4a7b8c058"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION enforce_finalized_journal_header_immutability()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $body$
    BEGIN
        IF OLD.status NOT IN ('posted', 'reversed') THEN
            RETURN NEW;
        END IF;

        IF OLD.status = 'reversed' THEN
            IF to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
                RAISE EXCEPTION
                    'Reversed journal header is immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END IF;

        IF OLD.status = 'posted'
           AND NEW.status = 'reversed'
        THEN
            IF NEW.reversed_at IS NULL
               OR NEW.reversed_by IS NULL
               OR (
                   to_jsonb(NEW)
                   - 'status'
                   - 'reversed_at'
                   - 'reversed_by'
               ) IS DISTINCT FROM (
                   to_jsonb(OLD)
                   - 'status'
                   - 'reversed_at'
                   - 'reversed_by'
               )
            THEN
                RAISE EXCEPTION
                    'Invalid posted-to-reversed journal transition'
                    USING ERRCODE = '23514';
            END IF;

            RETURN NEW;
        END IF;

        IF to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
            RAISE EXCEPTION
                'Posted journal header is immutable outside reversal'
                USING ERRCODE = '23514';
        END IF;

        RETURN NEW;
    END;
    $body$;
    """)

    op.execute("""
    CREATE TRIGGER trg_finalized_journal_header_immutability
    BEFORE UPDATE ON journal_entries
    FOR EACH ROW
    EXECUTE FUNCTION
        enforce_finalized_journal_header_immutability();
    """)


def downgrade():
    op.execute("""
    DROP TRIGGER trg_finalized_journal_header_immutability
    ON journal_entries
    """)

    op.execute("""
    DROP FUNCTION enforce_finalized_journal_header_immutability()
    """)
