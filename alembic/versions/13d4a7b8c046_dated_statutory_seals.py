"""Seal linked dated statutory evidence against late inserts.

Revision ID: 13d4a7b8c046
Revises: 13d4a7b8c045

Legacy unlinked statutory results are not sealed.
Existing linked evidence is sealed without modifying its rows.
"""

from alembic import op


revision = "13d4a7b8c046"
down_revision = "13d4a7b8c045"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE payroll_dated_statutory_seals (
            company_id integer NOT NULL,
            payroll_statutory_result_id integer NOT NULL,
            sealed_at timestamptz NOT NULL DEFAULT now(),

            CONSTRAINT pk_payroll_dated_statutory_seals
                PRIMARY KEY (
                    company_id,
                    payroll_statutory_result_id
                ),

            CONSTRAINT fk_payroll_dated_statutory_seals_result
                FOREIGN KEY (
                    company_id,
                    payroll_statutory_result_id
                )
                REFERENCES payroll_statutory_results (
                    company_id,
                    id
                )
                ON DELETE RESTRICT
        )
    """)

    # Preserve legacy data and seal existing linked evidence.
    # No existing header, line or source row is updated.
    op.execute("""
        INSERT INTO payroll_dated_statutory_seals (
            company_id,
            payroll_statutory_result_id
        )
        SELECT DISTINCT
            h.company_id,
            h.id
        FROM payroll_statutory_results h
        JOIN payroll_statutory_result_lines l
          ON l.company_id = h.company_id
         AND l.payroll_statutory_result_id = h.id
        JOIN payroll_statutory_line_sources s
          ON s.company_id = l.company_id
         AND s.statutory_line_id = l.id
    """)

    op.execute("""
        CREATE FUNCTION erp_guard_dated_statutory_late_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            target_result_id integer;
            is_sealed boolean;
        BEGIN
            IF TG_TABLE_NAME =
                'payroll_statutory_result_lines'
            THEN
                target_result_id :=
                    NEW.payroll_statutory_result_id;

            ELSIF TG_TABLE_NAME =
                'payroll_statutory_line_sources'
            THEN
                SELECT l.payroll_statutory_result_id
                  INTO target_result_id
                FROM payroll_statutory_result_lines l
                WHERE l.company_id = NEW.company_id
                  AND l.id = NEW.statutory_line_id;

            ELSE
                RAISE EXCEPTION
                    'Unexpected statutory INSERT guard table'
                    USING ERRCODE = '23514';
            END IF;

            IF target_result_id IS NULL THEN
                RETURN NEW;
            END IF;

            SELECT EXISTS (
                SELECT 1
                FROM payroll_dated_statutory_seals sealed
                WHERE sealed.company_id = NEW.company_id
                  AND sealed.payroll_statutory_result_id =
                      target_result_id
            )
            INTO is_sealed;

            IF is_sealed THEN
                RAISE EXCEPTION
                    'Sealed dated statutory evidence cannot receive new rows'
                    USING ERRCODE = '23514';
            END IF;

            RETURN NEW;
        END;
        $$
    """)

    op.execute("""
        CREATE TRIGGER trg_dated_statutory_line_no_late_insert
        BEFORE INSERT ON payroll_statutory_result_lines
        FOR EACH ROW
        EXECUTE FUNCTION erp_guard_dated_statutory_late_insert()
    """)

    op.execute("""
        CREATE TRIGGER trg_dated_statutory_source_no_late_insert
        BEFORE INSERT ON payroll_statutory_line_sources
        FOR EACH ROW
        EXECUTE FUNCTION erp_guard_dated_statutory_late_insert()
    """)

    # A seal must not be removed or modified after creation.
    op.execute("""
        CREATE FUNCTION erp_guard_dated_statutory_seal_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'Dated statutory seal is immutable'
                USING ERRCODE = '23514';
        END;
        $$
    """)

    op.execute("""
        CREATE TRIGGER trg_dated_statutory_seal_immutable
        BEFORE UPDATE OR DELETE
        ON payroll_dated_statutory_seals
        FOR EACH ROW
        EXECUTE FUNCTION erp_guard_dated_statutory_seal_immutable()
    """)


def downgrade():
    op.execute("""
        DROP TRIGGER trg_dated_statutory_seal_immutable
        ON payroll_dated_statutory_seals
    """)

    op.execute("""
        DROP TRIGGER trg_dated_statutory_source_no_late_insert
        ON payroll_statutory_line_sources
    """)

    op.execute("""
        DROP TRIGGER trg_dated_statutory_line_no_late_insert
        ON payroll_statutory_result_lines
    """)

    op.execute("""
        DROP FUNCTION erp_guard_dated_statutory_seal_immutable()
    """)

    op.execute("""
        DROP FUNCTION erp_guard_dated_statutory_late_insert()
    """)

    op.execute("""
        DROP TABLE payroll_dated_statutory_seals
    """)
