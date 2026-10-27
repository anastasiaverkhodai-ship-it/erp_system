"""Protect linked dated statutory evidence from mutation.

Revision ID: 13d4a7b8c045
Revises: 13d4a7b8c044

Legacy unlinked statutory results retain existing behavior.
"""

from alembic import op

revision = "13d4a7b8c045"
down_revision = "13d4a7b8c044"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE FUNCTION erp_guard_dated_statutory_evidence()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            linked boolean := false;
        BEGIN
            IF TG_TABLE_NAME = 'payroll_statutory_line_sources' THEN
                linked := true;

            ELSIF TG_TABLE_NAME = 'payroll_statutory_result_lines' THEN
                SELECT EXISTS (
                    SELECT 1
                    FROM payroll_statutory_line_sources s
                    WHERE s.company_id = OLD.company_id
                      AND s.statutory_line_id = OLD.id
                ) INTO linked;

            ELSIF TG_TABLE_NAME = 'payroll_statutory_results' THEN
                SELECT EXISTS (
                    SELECT 1
                    FROM payroll_statutory_result_lines l
                    JOIN payroll_statutory_line_sources s
                      ON s.company_id = l.company_id
                     AND s.statutory_line_id = l.id
                    WHERE l.company_id = OLD.company_id
                      AND l.payroll_statutory_result_id = OLD.id
                ) INTO linked;

            ELSIF TG_TABLE_NAME = 'payroll_calculation_lines' THEN
                SELECT EXISTS (
                    SELECT 1
                    FROM payroll_statutory_line_sources s
                    WHERE s.company_id = OLD.company_id
                      AND s.payroll_calculation_line_id = OLD.id
                ) INTO linked;

            ELSIF TG_TABLE_NAME = 'payroll_statutory_rates' THEN
                SELECT EXISTS (
                    SELECT 1
                    FROM payroll_statutory_result_lines l
                    JOIN payroll_statutory_line_sources s
                      ON s.company_id = l.company_id
                     AND s.statutory_line_id = l.id
                    WHERE l.company_id = OLD.company_id
                      AND l.source_rate_id = OLD.id
                ) INTO linked;

            ELSE
                RAISE EXCEPTION 'Unexpected statutory guard table'
                    USING ERRCODE = '23514';
            END IF;

            IF linked THEN
                RAISE EXCEPTION
                    'Immutable dated statutory evidence cannot be changed'
                    USING ERRCODE = '23514';
            END IF;

            RETURN OLD;
        END;
        $$
    """)

    op.execute("""
        CREATE TRIGGER trg_dated_statutory_header_immutable
        BEFORE UPDATE OR DELETE ON payroll_statutory_results
        FOR EACH ROW
        EXECUTE FUNCTION erp_guard_dated_statutory_evidence()
    """)

    op.execute("""
        CREATE TRIGGER trg_dated_statutory_line_immutable
        BEFORE UPDATE OR DELETE ON payroll_statutory_result_lines
        FOR EACH ROW
        EXECUTE FUNCTION erp_guard_dated_statutory_evidence()
    """)

    op.execute("""
        CREATE TRIGGER trg_dated_statutory_source_link_immutable
        BEFORE UPDATE OR DELETE ON payroll_statutory_line_sources
        FOR EACH ROW
        EXECUTE FUNCTION erp_guard_dated_statutory_evidence()
    """)

    op.execute("""
        CREATE TRIGGER trg_dated_statutory_salary_source_immutable
        BEFORE UPDATE OR DELETE ON payroll_calculation_lines
        FOR EACH ROW
        EXECUTE FUNCTION erp_guard_dated_statutory_evidence()
    """)

    op.execute("""
        CREATE TRIGGER trg_dated_statutory_rate_immutable
        BEFORE UPDATE OR DELETE ON payroll_statutory_rates
        FOR EACH ROW
        EXECUTE FUNCTION erp_guard_dated_statutory_evidence()
    """)


def downgrade():
    op.execute("""
        DROP TRIGGER trg_dated_statutory_rate_immutable
        ON payroll_statutory_rates
    """)
    op.execute("""
        DROP TRIGGER trg_dated_statutory_salary_source_immutable
        ON payroll_calculation_lines
    """)
    op.execute("""
        DROP TRIGGER trg_dated_statutory_source_link_immutable
        ON payroll_statutory_line_sources
    """)
    op.execute("""
        DROP TRIGGER trg_dated_statutory_line_immutable
        ON payroll_statutory_result_lines
    """)
    op.execute("""
        DROP TRIGGER trg_dated_statutory_header_immutable
        ON payroll_statutory_results
    """)
    op.execute("""
        DROP FUNCTION erp_guard_dated_statutory_evidence()
    """)
