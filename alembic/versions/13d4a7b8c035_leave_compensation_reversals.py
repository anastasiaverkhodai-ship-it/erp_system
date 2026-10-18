"""Preserve compensation reversals and allow a corrected draft calculation."""
from alembic import op
revision = '13d4a7b8c035'
down_revision = '13d4a7b8c034'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(TABLE_SQL)
    op.drop_constraint('uq_plc_termination', 'payroll_leave_compensations', type_='unique')
    op.execute("""
CREATE OR REPLACE FUNCTION payroll_guard_leave_compensation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_xid bigint;
BEGIN
 IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'Leave compensation history is immutable'; END IF;
 PERFORM id FROM companies WHERE id=NEW.company_id FOR UPDATE;
 IF TG_TABLE_NAME='payroll_leave_compensation_lines' THEN
  SELECT creation_xid INTO parent_xid FROM payroll_leave_compensations WHERE company_id=NEW.company_id AND id=NEW.compensation_id;
  IF parent_xid IS DISTINCT FROM txid_current() THEN RAISE EXCEPTION 'Cannot append to sealed leave compensation'; END IF;
 ELSIF TG_TABLE_NAME='payroll_leave_compensations' THEN
  NEW.creation_xid := txid_current();
  IF EXISTS (SELECT 1 FROM payroll_leave_compensations c WHERE c.company_id=NEW.company_id
    AND c.termination_event_id=NEW.termination_event_id AND NOT EXISTS (
      SELECT 1 FROM payroll_leave_compensation_reversals r WHERE r.company_id=c.company_id AND r.compensation_id=c.id
    )) THEN RAISE EXCEPTION 'Termination already has active compensation'; END IF;
 ELSE
  IF NOT EXISTS (SELECT 1 FROM payroll_leave_compensations c
    JOIN payroll_inputs i ON i.company_id=c.company_id AND i.id=c.payroll_input_id
    JOIN payroll_periods p ON p.company_id=i.company_id AND p.id=i.payroll_period_id
    WHERE c.company_id=NEW.company_id AND c.id=NEW.compensation_id
      AND p.status='draft' AND NEW.effective_date>=c.compensation_date
      AND NOT EXISTS (SELECT 1 FROM payroll_calculations pc WHERE pc.company_id=c.company_id
        AND pc.payroll_input_id=c.payroll_input_id)
  ) THEN RAISE EXCEPTION 'Only unused draft compensation may be reversed'; END IF;
 END IF;
 RETURN NEW;
END; $$;
""")
    op.execute('CREATE TRIGGER trg_plcr_write BEFORE INSERT OR UPDATE OR DELETE ON payroll_leave_compensation_reversals FOR EACH ROW EXECUTE FUNCTION payroll_guard_leave_compensation()')
    op.execute('CREATE TRIGGER trg_plcr_truncate BEFORE TRUNCATE ON payroll_leave_compensation_reversals FOR EACH STATEMENT EXECUTE FUNCTION payroll_guard_leave_compensation()')
    op.execute("""
CREATE FUNCTION payroll_validate_leave_compensation_reversal() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS (
   SELECT 1 FROM payroll_leave_compensation_lines l
   LEFT JOIN payroll_leave_balance_entries b ON b.company_id=l.company_id AND b.reversal_of_id=l.balance_entry_id
   WHERE l.company_id=NEW.company_id AND l.compensation_id=NEW.compensation_id
   AND (b.id IS NULL OR b.kind<>'reversal' OR b.days<>l.consumed_days
     OR b.account_id<>l.account_id OR b.acquisition_bucket<>l.acquisition_bucket
     OR b.effective_date<>NEW.effective_date)
 ) THEN RAISE EXCEPTION 'Compensation reversal must restore all consumed rights'; END IF;
 RETURN NULL;
END; $$;
""")
    op.execute('CREATE CONSTRAINT TRIGGER trg_plcr_reconcile AFTER INSERT ON payroll_leave_compensation_reversals DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION payroll_validate_leave_compensation_reversal()')


def downgrade():
    op.execute('LOCK TABLE payroll_leave_compensations, payroll_leave_compensation_reversals IN ACCESS EXCLUSIVE MODE')
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM payroll_leave_compensation_reversals) THEN RAISE EXCEPTION 'Compensation reversal history must be preserved'; END IF; END $$")
    op.drop_table('payroll_leave_compensation_reversals')
    op.execute('DROP FUNCTION payroll_validate_leave_compensation_reversal()')
    op.create_unique_constraint('uq_plc_termination', 'payroll_leave_compensations', ['company_id','termination_event_id'])
    op.execute(ORIGINAL_GUARD)

TABLE_SQL = '\nCREATE TABLE payroll_leave_compensation_reversals (\n\tid SERIAL NOT NULL, \n\tcompany_id INTEGER NOT NULL, \n\tcompensation_id INTEGER NOT NULL, \n\teffective_date DATE NOT NULL, \n\tsource_reference VARCHAR(1000) NOT NULL, \n\trequest_key VARCHAR(100) NOT NULL, \n\trequest_fingerprint VARCHAR(64) NOT NULL, \n\tcreated_by INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_plcr_source UNIQUE (company_id, compensation_id), \n\tCONSTRAINT uq_plcr_request UNIQUE (company_id, request_key), \n\tCONSTRAINT fk_plcr_compensation FOREIGN KEY(company_id, compensation_id) REFERENCES payroll_leave_compensations (company_id, id) ON DELETE RESTRICT, \n\tFOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT\n)\n\n'
ORIGINAL_GUARD = "CREATE OR REPLACE FUNCTION payroll_guard_leave_compensation() RETURNS trigger LANGUAGE plpgsql AS $$\nDECLARE parent_xid bigint;\nBEGIN\n IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'Leave compensation history is immutable'; END IF;\n PERFORM id FROM companies WHERE id=NEW.company_id FOR UPDATE;\n IF TG_TABLE_NAME='payroll_leave_compensation_lines' THEN\n  SELECT creation_xid INTO parent_xid FROM payroll_leave_compensations WHERE company_id=NEW.company_id AND id=NEW.compensation_id;\n  IF parent_xid IS DISTINCT FROM txid_current() THEN RAISE EXCEPTION 'Cannot append to sealed leave compensation'; END IF;\n END IF;\n RETURN NEW;\nEND; $$;"
