"""Serialize salary-slice membership and seal calculation lines at commit.

Revision ID: 13d4a7b8c027
Revises: 13d4a7b8c026

Legacy calculations have no creation transaction and are already sealed.
New calculations accept initial lines only in their creating transaction.
These guards do not certify legacy historical provenance.
"""
from alembic import op

revision = "13d4a7b8c027"
down_revision = "13d4a7b8c026"
branch_labels = None
depends_on = None

TRUNCATE_GUARD_SQL = """
CREATE FUNCTION payroll_reject_frozen_membership_truncate() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Historical payroll membership cannot be truncated'
        USING ERRCODE = '23514';
END;
$$
"""

SLICE_GUARD_SQL = """
CREATE FUNCTION payroll_guard_salary_slice_membership() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    parent record;
    old_id integer;
    new_id integer;
BEGIN
    IF TG_OP <> 'INSERT' THEN old_id := OLD.payroll_input_id; END IF;
    IF TG_OP <> 'DELETE' THEN new_id := NEW.payroll_input_id; END IF;
    -- Mutating the shared parent serializes both directions of the race.
    -- A stale REPEATABLE READ snapshot fails with serialization_failure.
    FOR parent IN
        SELECT id, company_id, history_sealed FROM payroll_inputs
        WHERE id IN (old_id, new_id) ORDER BY id FOR UPDATE
    LOOP
        IF parent.history_sealed THEN
            RAISE EXCEPTION 'Historical salary slice membership is immutable'
                USING ERRCODE = '23514';
        END IF;
        UPDATE payroll_inputs SET history_epoch = history_epoch + 1
        WHERE id = parent.id;
    END LOOP;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$
"""

CALCULATION_GUARD_SQL = """
CREATE FUNCTION payroll_freeze_input_on_calculation() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    sealed boolean;
BEGIN
    SELECT history_sealed INTO sealed FROM payroll_inputs
    WHERE id = NEW.payroll_input_id AND company_id = NEW.company_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Payroll input identity mismatch' USING ERRCODE = '23514';
    END IF;
    IF NOT sealed THEN
        UPDATE payroll_inputs SET history_sealed = TRUE,
            history_epoch = history_epoch + 1
        WHERE id = NEW.payroll_input_id AND company_id = NEW.company_id;
    END IF;
    NEW.history_creation_xid := pg_current_xact_id();
    RETURN NEW;
END;
$$
"""

LINE_GUARD_SQL = """
CREATE FUNCTION payroll_guard_initial_line_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    creation_xid xid8;
BEGIN
    SELECT history_creation_xid INTO creation_xid FROM payroll_calculations
    WHERE id = NEW.payroll_calculation_id AND company_id = NEW.company_id;
    IF NOT FOUND OR creation_xid IS DISTINCT FROM pg_current_xact_id() THEN
        RAISE EXCEPTION 'Historical payroll calculation lines are sealed'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$
"""


def upgrade():
    op.execute("ALTER TABLE payroll_inputs ADD COLUMN history_sealed boolean NOT NULL DEFAULT FALSE, ADD COLUMN history_epoch bigint NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE payroll_calculations ADD COLUMN history_creation_xid xid8")
    # ALTER holds an exclusive lock until commit; no concurrent writer can
    # pass through this transactional backfill interval.
    op.execute("DROP TRIGGER trg_payroll_inputs_history_freeze ON payroll_inputs")
    op.execute("""UPDATE payroll_inputs i SET history_sealed = TRUE
        WHERE EXISTS (SELECT 1 FROM payroll_calculations c
        WHERE c.payroll_input_id = i.id AND c.company_id = i.company_id)""")
    op.execute("""CREATE TRIGGER trg_payroll_inputs_history_freeze
        BEFORE UPDATE OR DELETE ON payroll_inputs FOR EACH ROW
        EXECUTE FUNCTION payroll_reject_used_source_mutation()""")
    for sql in (SLICE_GUARD_SQL, CALCULATION_GUARD_SQL, LINE_GUARD_SQL):
        op.execute(sql)
    op.execute("""CREATE TRIGGER trg_salary_slice_membership
        BEFORE INSERT OR UPDATE OR DELETE ON payroll_input_salary_slices
        FOR EACH ROW EXECUTE FUNCTION payroll_guard_salary_slice_membership()""")
    op.execute("""CREATE TRIGGER trg_calculation_freeze_input
        BEFORE INSERT ON payroll_calculations
        FOR EACH ROW EXECUTE FUNCTION payroll_freeze_input_on_calculation()""")
    op.execute("""CREATE TRIGGER trg_calculation_initial_lines
        BEFORE INSERT ON payroll_calculation_lines
        FOR EACH ROW EXECUTE FUNCTION payroll_guard_initial_line_insert()""")
    op.execute(TRUNCATE_GUARD_SQL)
    for table in ("payroll_input_salary_slices", "payroll_calculation_lines"):
        op.execute(f"""CREATE TRIGGER trg_{table}_membership_no_truncate
            BEFORE TRUNCATE ON {table} FOR EACH STATEMENT
            EXECUTE FUNCTION payroll_reject_frozen_membership_truncate()""")


def downgrade():
    for table in ("payroll_input_salary_slices", "payroll_calculation_lines"):
        op.execute(f"DROP TRIGGER trg_{table}_membership_no_truncate ON {table}")
    op.execute("DROP FUNCTION payroll_reject_frozen_membership_truncate()")
    for trigger, table in (
        ("trg_calculation_initial_lines", "payroll_calculation_lines"),
        ("trg_calculation_freeze_input", "payroll_calculations"),
        ("trg_salary_slice_membership", "payroll_input_salary_slices"),
    ):
        op.execute(f"DROP TRIGGER {trigger} ON {table}")
    for function in (
        "payroll_guard_initial_line_insert", "payroll_freeze_input_on_calculation",
        "payroll_guard_salary_slice_membership",
    ):
        op.execute(f"DROP FUNCTION {function}()")
    op.execute("ALTER TABLE payroll_calculations DROP COLUMN history_creation_xid")
    op.execute("ALTER TABLE payroll_inputs DROP COLUMN history_epoch, DROP COLUMN history_sealed")
