"""Seal new correction bases; never attest pre-existing calculations.

Revision ID: 13d4a7b8c028
Revises: 13d4a7b8c027
"""
from alembic import op
import sqlalchemy as sa
revision = '13d4a7b8c028'
down_revision = '13d4a7b8c027'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION payroll_seal_correction_basis() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.minimum_wage_assessment::jsonb ? 'correction_basis' THEN
        IF NEW.minimum_wage_assessment::jsonb #>> '{correction_basis,payload,schema}'
             IS DISTINCT FROM 'payroll-minimum-context-basis-v1' THEN
          RAISE EXCEPTION 'Unknown correction basis schema';
        END IF;
        NEW.minimum_wage_assessment := jsonb_set(NEW.minimum_wage_assessment::jsonb,
          '{correction_basis,seal}', '"creation-028-v1"'::jsonb);
      END IF;
      RETURN NEW;
    END; $$;
    """)
    op.execute("""
    CREATE TRIGGER trg_payroll_correction_basis_seal BEFORE INSERT ON payroll_calculations
      FOR EACH ROW EXECUTE FUNCTION payroll_seal_correction_basis();
    """)
    op.execute("""
    CREATE TRIGGER trg_payroll_calculations_no_truncate BEFORE TRUNCATE ON payroll_calculations
      FOR EACH STATEMENT EXECUTE FUNCTION payroll_reject_used_source_mutation();
    """)


def downgrade():
    op.execute('LOCK TABLE payroll_calculations IN ACCESS EXCLUSIVE MODE')
    if op.get_bind().scalar(sa.text("""SELECT EXISTS (SELECT 1 FROM payroll_calculations
       WHERE minimum_wage_assessment::jsonb #>> '{correction_basis,seal}' = 'creation-028-v1')""")):
        raise RuntimeError('Cannot remove correction basis protection while sealed history exists')
    op.execute('DROP TRIGGER trg_payroll_calculations_no_truncate ON payroll_calculations')
    op.execute('DROP TRIGGER trg_payroll_correction_basis_seal ON payroll_calculations')
    op.execute('DROP FUNCTION payroll_seal_correction_basis()')
