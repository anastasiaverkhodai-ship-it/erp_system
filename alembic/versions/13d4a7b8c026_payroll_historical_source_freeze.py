"""Protect referenced historical payroll sources.

Revision ID: 13d4a7b8c026
Revises: 13d4a7b8c025

Scope:
- Reject UPDATE/DELETE of payroll calculations and their lines.
- Reject UPDATE/DELETE of source rows already used by calculations.
- Preserve ordinary mutation of unreferenced sources.

This is not, by itself, a complete historical-provenance guarantee.
INSERT/backdating and concurrent mutation require further guards.
"""

from alembic import op


revision = "13d4a7b8c026"
down_revision = "13d4a7b8c025"
branch_labels = None
depends_on = None


TRIGGER_FUNCTION = "payroll_reject_used_source_mutation"


PROTECTED_TABLES = (
    "payroll_calculations",
    "payroll_calculation_lines",
    "payroll_inputs",
    "payroll_input_salary_slices",
    "employee_salary_rates",
    "payroll_supplements",
    "payroll_vacation_calculations",
    "payroll_sick_leave_calculations",
    "leave_requests",
)


CREATE_FUNCTION_SQL = """
CREATE FUNCTION payroll_reject_used_source_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    protected_row boolean := FALSE;
BEGIN
    IF TG_OP NOT IN ('UPDATE', 'DELETE') THEN
        RAISE EXCEPTION
            'Unsupported payroll historical freeze operation: %',
            TG_OP;
    END IF;

    IF TG_TABLE_NAME IN (
        'payroll_calculations',
        'payroll_calculation_lines'
    ) THEN

        protected_row := TRUE;

    ELSIF TG_TABLE_NAME = 'payroll_inputs' THEN

        SELECT EXISTS (
            SELECT 1
            FROM payroll_calculations c
            WHERE c.company_id = OLD.company_id
              AND c.payroll_input_id = OLD.id
        )
        INTO protected_row;

    ELSIF TG_TABLE_NAME = 'payroll_input_salary_slices' THEN

        SELECT EXISTS (
            SELECT 1
            FROM payroll_calculations c
            WHERE c.company_id = OLD.company_id
              AND c.payroll_input_id = OLD.payroll_input_id
        )
        INTO protected_row;

    ELSIF TG_TABLE_NAME = 'employee_salary_rates' THEN

        SELECT EXISTS (
            SELECT 1
            FROM payroll_input_salary_slices s
            JOIN payroll_calculations c
              ON c.company_id = s.company_id
             AND c.payroll_input_id = s.payroll_input_id
            WHERE s.company_id = OLD.company_id
              AND s.salary_rate_id = OLD.id
        )
        INTO protected_row;

    ELSIF TG_TABLE_NAME = 'payroll_supplements' THEN

        SELECT EXISTS (
            SELECT 1
            FROM payroll_calculation_lines l
            WHERE l.company_id = OLD.company_id
              AND l.source_supplement_id = OLD.id
        )
        INTO protected_row;

    ELSIF TG_TABLE_NAME = 'payroll_vacation_calculations' THEN

        SELECT EXISTS (
            SELECT 1
            FROM payroll_calculation_lines l
            WHERE l.company_id = OLD.company_id
              AND l.source_vacation_calculation_id = OLD.id
        )
        INTO protected_row;

    ELSIF TG_TABLE_NAME = 'payroll_sick_leave_calculations' THEN

        SELECT EXISTS (
            SELECT 1
            FROM payroll_calculation_lines l
            WHERE l.company_id = OLD.company_id
              AND l.source_sick_leave_calculation_id = OLD.id
        )
        INTO protected_row;

    ELSIF TG_TABLE_NAME = 'leave_requests' THEN

        SELECT EXISTS (
            SELECT 1
            FROM payroll_vacation_calculations v
            JOIN payroll_calculation_lines l
              ON l.company_id = v.company_id
             AND l.source_vacation_calculation_id = v.id
            WHERE v.company_id = OLD.company_id
              AND v.leave_request_id = OLD.id
        )
        OR EXISTS (
            SELECT 1
            FROM payroll_sick_leave_calculations s
            JOIN payroll_calculation_lines l
              ON l.company_id = s.company_id
             AND l.source_sick_leave_calculation_id = s.id
            WHERE s.company_id = OLD.company_id
              AND s.leave_request_id = OLD.id
        )
        INTO protected_row;

    ELSE
        RAISE EXCEPTION
            'Unknown historical payroll source table: %',
            TG_TABLE_NAME;
    END IF;

    IF protected_row THEN
        RAISE EXCEPTION
            'Historical payroll source is immutable: %',
            TG_TABLE_NAME
            USING ERRCODE = '23514';
    END IF;

    IF TG_OP = 'UPDATE' THEN
        RETURN NEW;
    END IF;

    RETURN OLD;
END;
$$
"""


def _trigger_name(table_name: str) -> str:
    name = "trg_" + table_name + "_history_freeze"

    if len(name.encode("utf-8")) > 63:
        raise ValueError(
            "PostgreSQL trigger name exceeds 63 bytes"
        )

    return name


def upgrade() -> None:
    op.execute(CREATE_FUNCTION_SQL)

    for table_name in PROTECTED_TABLES:
        trigger_name = _trigger_name(table_name)

        op.execute(
            f"""
            CREATE TRIGGER {trigger_name}
            BEFORE UPDATE OR DELETE
            ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION {TRIGGER_FUNCTION}()
            """
        )


def downgrade() -> None:
    for table_name in reversed(PROTECTED_TABLES):
        trigger_name = _trigger_name(table_name)

        op.execute(
            f"""
            DROP TRIGGER {trigger_name}
            ON {table_name}
            """
        )

    op.execute(
        f"DROP FUNCTION {TRIGGER_FUNCTION}()"
    )
