"""Versioned time-premium sources created by reviewed attendance corrections."""
from alembic import op
import sqlalchemy as sa
revision='13d4a7b8c068'
down_revision='13d4a7b8c067'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('payroll_supplements',sa.Column('correction_request_id',sa.Integer(),nullable=True))
    op.create_foreign_key('fk_supplement_attendance_request','payroll_supplements','payroll_attendance_correction_requests',
        ['company_id','correction_request_id'],['company_id','id'],ondelete='RESTRICT')
    op.execute("""CREATE FUNCTION erp_attendance_premium_complete() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.correction_request_id IS NULL THEN RETURN NEW; END IF;
        IF NEW.kind NOT IN ('night','overtime','rest_day','regular_extra') OR NOT EXISTS (
            SELECT 1 FROM payroll_attendance_correction_requests r
            JOIN payroll_attendance_corrections e ON e.company_id=r.company_id AND e.request_id=r.id
            JOIN payroll_sick_correction_applications a ON a.company_id=e.company_id AND a.id=e.application_id
            JOIN payroll_calculations original_calc ON original_calc.company_id=r.company_id AND original_calc.id=r.calculation_id
            JOIN payroll_calculation_lines l ON l.company_id=a.company_id AND l.source_supplement_id=NEW.id
            WHERE r.company_id=NEW.company_id AND r.id=NEW.correction_request_id
                AND e.reviewed_by=NEW.created_by AND r.created_by<>e.reviewed_by
                AND a.source_kind='attendance'
                AND EXISTS (SELECT 1 FROM payroll_inputs pi WHERE pi.company_id=NEW.company_id
                    AND pi.id=NEW.payroll_input_id AND pi.employment_contract_id=original_calc.employment_contract_id)
                AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(a.payroll_calculation_ids::jsonb) x
                    WHERE x::integer=l.payroll_calculation_id
                    UNION ALL SELECT 1 FROM payroll_sick_correction_applications medical,
                        jsonb_array_elements_text(medical.payroll_calculation_ids::jsonb) y
                    WHERE medical.company_id=r.company_id AND medical.request_key='attendance-calendar:'||r.id::text
                        AND y::integer=l.payroll_calculation_id)) THEN
            RAISE EXCEPTION 'Corrected time premium requires completed independent attendance review';
        END IF;
        RETURN NEW;
    END $$""")
    op.execute('CREATE CONSTRAINT TRIGGER attendance_premium_complete AFTER INSERT ON payroll_supplements DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION erp_attendance_premium_complete()')
    op.execute("""CREATE TRIGGER attendance_premium_immutable BEFORE UPDATE OR DELETE ON payroll_supplements
        FOR EACH ROW WHEN (OLD.correction_request_id IS NOT NULL) EXECUTE FUNCTION erp_sick_application_immutable()""")
    op.execute('CREATE TRIGGER attendance_premiums_no_truncate BEFORE TRUNCATE ON payroll_supplements FOR EACH STATEMENT EXECUTE FUNCTION erp_sick_application_immutable()')


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM payroll_supplements WHERE correction_request_id IS NOT NULL)
        THEN RAISE EXCEPTION 'Cannot remove corrected time premium history'; END IF; END $$""")
    for name in ('attendance_premium_complete','attendance_premium_immutable','attendance_premiums_no_truncate'):
        op.execute(f'DROP TRIGGER {name} ON payroll_supplements')
    op.execute('DROP FUNCTION erp_attendance_premium_complete()')
    op.drop_constraint('fk_supplement_attendance_request','payroll_supplements',type_='foreignkey')
    op.drop_column('payroll_supplements','correction_request_id')
