"""Bind leave compensation to termination and payroll provenance."""
from alembic import op
import sqlalchemy as sa
revision = '13d4a7b8c034'
down_revision = '13d4a7b8c033'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("\nCREATE TABLE payroll_leave_compensations (\n\tid SERIAL NOT NULL, \n\tcompany_id INTEGER NOT NULL, \n\temployment_contract_id INTEGER NOT NULL, \n\ttermination_event_id INTEGER NOT NULL, \n\tpayroll_input_id INTEGER NOT NULL, \n\tcompensation_date DATE NOT NULL, \n\tcreation_xid BIGINT DEFAULT txid_current() NOT NULL, \n\tgross_amount NUMERIC(18, 2) NOT NULL, \n\tsource_reference VARCHAR(1000) NOT NULL, \n\trequest_key VARCHAR(100) NOT NULL, \n\trequest_fingerprint VARCHAR(64) NOT NULL, \n\tcreated_by INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_plc_company_id UNIQUE (company_id, id), \n\tCONSTRAINT uq_plc_termination UNIQUE (company_id, termination_event_id), \n\tCONSTRAINT uq_plc_request UNIQUE (company_id, request_key), \n\tCONSTRAINT fk_plc_contract FOREIGN KEY(company_id, employment_contract_id) REFERENCES employment_contracts (company_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_plc_termination FOREIGN KEY(company_id, termination_event_id) REFERENCES employment_events (company_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_plc_input FOREIGN KEY(company_id, payroll_input_id) REFERENCES payroll_inputs (company_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_plc_gross CHECK (gross_amount >= 0 AND gross_amount <> 'NaN'::numeric), \n\tFOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT\n)\n\n")
    op.execute("\nCREATE TABLE payroll_leave_compensation_lines (\n\tid SERIAL NOT NULL, \n\tcompany_id INTEGER NOT NULL, \n\tcompensation_id INTEGER NOT NULL, \n\taccount_id INTEGER NOT NULL, \n\tbalance_entry_id INTEGER NOT NULL, \n\tacquisition_bucket VARCHAR(16) NOT NULL, \n\tconsumed_days NUMERIC(16, 8) NOT NULL, \n\tcompensated_days NUMERIC(10, 2) NOT NULL, \n\tamount NUMERIC(18, 2) NOT NULL, \n\tbasis_snapshot JSON NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_plcl_company_id UNIQUE (company_id, id), \n\tCONSTRAINT uq_plcl_account_bucket UNIQUE (company_id, compensation_id, account_id, acquisition_bucket), \n\tCONSTRAINT fk_plcl_compensation FOREIGN KEY(company_id, compensation_id) REFERENCES payroll_leave_compensations (company_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_plcl_account FOREIGN KEY(company_id, account_id) REFERENCES payroll_leave_accounts (company_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_plcl_balance FOREIGN KEY(company_id, balance_entry_id) REFERENCES payroll_leave_balance_entries (company_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_plcl_balance UNIQUE (company_id, balance_entry_id), \n\tCONSTRAINT ck_plcl_bucket CHECK (acquisition_bucket IN ('before_2024','from_2024')), \n\tCONSTRAINT ck_plcl_amounts CHECK (consumed_days > 0 AND compensated_days >= 0 AND amount >= 0 AND consumed_days <> 'NaN'::numeric AND compensated_days <> 'NaN'::numeric AND amount <> 'NaN'::numeric)\n)\n\n")
    op.add_column('payroll_calculation_lines',sa.Column('source_leave_compensation_line_id',sa.Integer(),nullable=True))
    op.drop_constraint('ck_payroll_calculation_lines_type','payroll_calculation_lines',type_='check')
    op.create_check_constraint('ck_payroll_calculation_lines_type','payroll_calculation_lines',"line_type IN ('salary','manual_adjustment','supplement','vacation_pay','sick_pay','minimum_wage','leave_compensation')")
    op.drop_constraint('ck_payroll_calculation_lines_rate','payroll_calculation_lines',type_='check')
    op.create_check_constraint('ck_payroll_calculation_lines_rate','payroll_calculation_lines',"(line_type IN ('salary','supplement','vacation_pay','sick_pay','minimum_wage','leave_compensation') AND rate IS NOT NULL AND rate >= 0) OR (line_type = 'manual_adjustment' AND rate IS NOT NULL)")
    op.drop_constraint('ck_payroll_calculation_lines_amount','payroll_calculation_lines',type_='check')
    op.create_check_constraint('ck_payroll_calculation_lines_amount','payroll_calculation_lines',"(line_type IN ('salary','supplement','vacation_pay','sick_pay','minimum_wage','leave_compensation') AND amount >= 0) OR line_type = 'manual_adjustment'")
    op.create_foreign_key('fk_pcl_leave_compensation','payroll_calculation_lines','payroll_leave_compensation_lines',['company_id','source_leave_compensation_line_id'],['company_id','id'],ondelete='RESTRICT')
    op.create_check_constraint('ck_pcl_leave_compensation_source','payroll_calculation_lines',"(line_type='leave_compensation')=(source_leave_compensation_line_id IS NOT NULL)")
    op.execute("CREATE FUNCTION payroll_guard_leave_compensation() RETURNS trigger LANGUAGE plpgsql AS $$\nDECLARE parent_xid bigint;\nBEGIN\n IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'Leave compensation history is immutable'; END IF;\n PERFORM id FROM companies WHERE id=NEW.company_id FOR UPDATE;\n IF TG_TABLE_NAME='payroll_leave_compensation_lines' THEN\n  SELECT creation_xid INTO parent_xid FROM payroll_leave_compensations WHERE company_id=NEW.company_id AND id=NEW.compensation_id;\n  IF parent_xid IS DISTINCT FROM txid_current() THEN RAISE EXCEPTION 'Cannot append to sealed leave compensation'; END IF;\n END IF;\n RETURN NEW;\nEND; $$;")
    op.execute('CREATE TRIGGER trg_plc0_write BEFORE INSERT OR UPDATE OR DELETE ON payroll_leave_compensations FOR EACH ROW EXECUTE FUNCTION payroll_guard_leave_compensation()')
    op.execute('CREATE TRIGGER trg_plc0_truncate BEFORE TRUNCATE ON payroll_leave_compensations FOR EACH STATEMENT EXECUTE FUNCTION payroll_guard_leave_compensation()')
    op.execute('CREATE TRIGGER trg_plc1_write BEFORE INSERT OR UPDATE OR DELETE ON payroll_leave_compensation_lines FOR EACH ROW EXECUTE FUNCTION payroll_guard_leave_compensation()')
    op.execute('CREATE TRIGGER trg_plc1_truncate BEFORE TRUNCATE ON payroll_leave_compensation_lines FOR EACH STATEMENT EXECUTE FUNCTION payroll_guard_leave_compensation()')

    op.execute("""
CREATE FUNCTION payroll_validate_leave_compensation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE header payroll_leave_compensations%ROWTYPE; header_id integer;
BEGIN
 IF TG_TABLE_NAME='payroll_leave_compensations' THEN header_id:=NEW.id;
 ELSE header_id:=NEW.compensation_id; END IF;
 SELECT * INTO header FROM payroll_leave_compensations
 WHERE company_id=NEW.company_id AND id=header_id;
 IF header.gross_amount IS DISTINCT FROM (
   SELECT COALESCE(sum(amount),0) FROM payroll_leave_compensation_lines
   WHERE company_id=header.company_id AND compensation_id=header.id
 ) THEN RAISE EXCEPTION 'Leave compensation total differs from its lines'; END IF;
 IF NOT EXISTS (
   SELECT 1 FROM employment_events e JOIN payroll_inputs i
   ON i.company_id=e.company_id AND i.employment_contract_id=e.employment_contract_id
   WHERE e.company_id=header.company_id AND e.id=header.termination_event_id
   AND e.event_type='termination' AND e.effective_date=header.compensation_date
   AND e.employment_contract_id=header.employment_contract_id AND i.id=header.payroll_input_id
 ) THEN RAISE EXCEPTION 'Leave compensation employment sources do not match'; END IF;
 IF EXISTS (
   SELECT 1 FROM payroll_leave_compensation_lines l
   JOIN payroll_leave_accounts a ON a.company_id=l.company_id AND a.id=l.account_id
   JOIN payroll_leave_balance_entries b ON b.company_id=l.company_id AND b.id=l.balance_entry_id
   WHERE l.company_id=header.company_id AND l.compensation_id=header.id AND (
     a.employment_contract_id<>header.employment_contract_id OR b.account_id<>l.account_id
     OR b.kind<>'compensation' OR b.days<>-l.consumed_days
     OR b.acquisition_bucket<>l.acquisition_bucket
     OR b.termination_event_id IS DISTINCT FROM header.termination_event_id
     OR b.effective_date<>header.compensation_date
   )
 ) THEN RAISE EXCEPTION 'Leave compensation does not match consumed rights'; END IF;
 RETURN NULL;
END; $$;
""")
    for table in ('payroll_leave_compensations', 'payroll_leave_compensation_lines'):
        op.execute(f'CREATE CONSTRAINT TRIGGER trg_{table}_reconcile AFTER INSERT ON {table} '
                   'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW '
                   'EXECUTE FUNCTION payroll_validate_leave_compensation()')


def downgrade():
    op.execute('LOCK TABLE payroll_leave_compensations IN ACCESS EXCLUSIVE MODE')
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM payroll_leave_compensations) THEN RAISE EXCEPTION 'Leave compensation history must be preserved'; END IF; END $$")
    op.execute('LOCK TABLE payroll_leave_compensation_lines IN ACCESS EXCLUSIVE MODE')
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM payroll_leave_compensation_lines) THEN RAISE EXCEPTION 'Leave compensation history must be preserved'; END IF; END $$")
    op.drop_constraint('ck_pcl_leave_compensation_source','payroll_calculation_lines',type_='check')
    op.drop_constraint('fk_pcl_leave_compensation','payroll_calculation_lines',type_='foreignkey')
    op.drop_column('payroll_calculation_lines','source_leave_compensation_line_id')
    op.drop_constraint('ck_payroll_calculation_lines_type','payroll_calculation_lines',type_='check')
    op.create_check_constraint('ck_payroll_calculation_lines_type','payroll_calculation_lines',"line_type IN ('salary','manual_adjustment','supplement','vacation_pay','sick_pay','minimum_wage')")
    op.drop_constraint('ck_payroll_calculation_lines_rate','payroll_calculation_lines',type_='check')
    op.create_check_constraint('ck_payroll_calculation_lines_rate','payroll_calculation_lines',"(line_type IN ('salary','supplement','vacation_pay','sick_pay','minimum_wage') AND rate IS NOT NULL AND rate >= 0) OR (line_type = 'manual_adjustment' AND rate IS NOT NULL)")
    op.drop_constraint('ck_payroll_calculation_lines_amount','payroll_calculation_lines',type_='check')
    op.create_check_constraint('ck_payroll_calculation_lines_amount','payroll_calculation_lines',"(line_type IN ('salary','supplement','vacation_pay','sick_pay','minimum_wage') AND amount >= 0) OR line_type = 'manual_adjustment'")
    op.drop_table('payroll_leave_compensation_lines')
    op.drop_table('payroll_leave_compensations')
    op.execute('DROP FUNCTION payroll_guard_leave_compensation()')
    op.execute('DROP FUNCTION payroll_validate_leave_compensation()')
