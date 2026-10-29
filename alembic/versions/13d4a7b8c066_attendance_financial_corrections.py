"""Reviewed immutable attendance corrections and financial revision evidence."""
from alembic import op
import sqlalchemy as sa
revision='13d4a7b8c066'
down_revision='13d4a7b8c065'
branch_labels=None
depends_on=None


def upgrade():
    op.drop_constraint('ck_payroll_correction_source_kind','payroll_sick_correction_applications',type_='check')
    op.create_check_constraint('ck_payroll_correction_source_kind','payroll_sick_correction_applications',
        "(source_kind='sick' AND original_sick_id IS NOT NULL AND replacement_sick_id IS NOT NULL) OR (source_kind IN ('leave','attendance') AND original_sick_id IS NULL AND replacement_sick_id IS NULL)")
    op.create_table('payroll_attendance_correction_requests',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('company_id',sa.Integer(),sa.ForeignKey('companies.id'),nullable=False),
        sa.Column('calculation_id',sa.Integer(),nullable=False),
        sa.Column('request_key',sa.String(90),nullable=False),sa.Column('fingerprint',sa.String(64),nullable=False),
        sa.Column('payload',sa.JSON(),nullable=False),
        sa.Column('created_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.UniqueConstraint('company_id','id',name='uq_attendance_request_company'),
        sa.UniqueConstraint('company_id','request_key',name='uq_attendance_request_key'),
        sa.ForeignKeyConstraint(['company_id','calculation_id'],['payroll_calculations.company_id','payroll_calculations.id'],name='fk_attendance_request_calculation'))
    op.create_table('payroll_attendance_corrections',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('company_id',sa.Integer(),sa.ForeignKey('companies.id'),nullable=False),
        sa.Column('request_id',sa.Integer(),nullable=False),sa.Column('application_id',sa.Integer(),nullable=False),
        sa.Column('reviewed_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('reviewed_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.UniqueConstraint('company_id','id',name='uq_attendance_correction_company'),
        sa.UniqueConstraint('company_id','request_id',name='uq_attendance_correction_request'),
        sa.UniqueConstraint('company_id','application_id',name='uq_attendance_correction_application'),
        sa.ForeignKeyConstraint(['company_id','request_id'],['payroll_attendance_correction_requests.company_id','payroll_attendance_correction_requests.id'],name='fk_attendance_correction_request'),
        sa.ForeignKeyConstraint(['company_id','application_id'],['payroll_sick_correction_applications.company_id','payroll_sick_correction_applications.id'],name='fk_attendance_correction_application'))
    for table in ('payroll_attendance_correction_requests','payroll_attendance_corrections'):
        op.execute(f'CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION erp_sick_application_immutable()')
        op.execute(f'CREATE TRIGGER {table}_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION erp_sick_application_immutable()')
    op.execute('''CREATE FUNCTION erp_attendance_correction_complete() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE evidence payroll_attendance_corrections%ROWTYPE; request payroll_attendance_correction_requests%ROWTYPE;
            app payroll_sick_correction_applications%ROWTYPE;
    BEGIN
      IF TG_TABLE_NAME='payroll_sick_correction_applications' THEN
        IF NEW.source_kind <> 'attendance' THEN RETURN NEW; END IF;
        SELECT * INTO evidence FROM payroll_attendance_corrections WHERE company_id=NEW.company_id AND application_id=NEW.id;
        IF NOT FOUND THEN RAISE EXCEPTION 'Attendance correction requires reviewed evidence'; END IF;
      ELSE SELECT * INTO evidence FROM payroll_attendance_corrections WHERE id=NEW.id; END IF;
      SELECT * INTO app FROM payroll_sick_correction_applications WHERE company_id=evidence.company_id AND id=evidence.application_id;
      SELECT * INTO request FROM payroll_attendance_correction_requests WHERE company_id=evidence.company_id AND id=evidence.request_id;
      IF app.source_kind IS DISTINCT FROM 'attendance' OR request.created_by=evidence.reviewed_by
          OR request.created_by IS DISTINCT FROM app.created_by OR request.fingerprint IS DISTINCT FROM app.request_fingerprint
          OR (request.payload->>'correction_date')::date IS DISTINCT FROM app.correction_date
          OR NOT EXISTS (SELECT 1 FROM payroll_corrections c WHERE c.company_id=request.company_id
             AND c.original_payroll_calculation_id=request.calculation_id
             AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(app.payroll_calculation_ids::jsonb) x
                         WHERE x::integer=c.replacement_payroll_calculation_id)) THEN
        RAISE EXCEPTION 'Attendance correction requires matching independent review and payroll revision';
      END IF;
      RETURN NEW;
    END $$''')
    for table in ('payroll_sick_correction_applications','payroll_attendance_corrections'):
        op.execute(f'CREATE CONSTRAINT TRIGGER attendance_review_complete AFTER INSERT ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION erp_attendance_correction_complete()')


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM payroll_attendance_correction_requests)
        THEN RAISE EXCEPTION 'Cannot remove attendance correction history'; END IF; END $$""")
    op.execute('DROP TRIGGER attendance_review_complete ON payroll_sick_correction_applications')
    op.drop_table('payroll_attendance_corrections')
    op.drop_table('payroll_attendance_correction_requests')
    op.execute('DROP FUNCTION erp_attendance_correction_complete()')
    op.drop_constraint('ck_payroll_correction_source_kind','payroll_sick_correction_applications',type_='check')
    op.create_check_constraint('ck_payroll_correction_source_kind','payroll_sick_correction_applications',
        "(source_kind='sick' AND original_sick_id IS NOT NULL AND replacement_sick_id IS NOT NULL) OR (source_kind='leave' AND original_sick_id IS NULL AND replacement_sick_id IS NULL)")
