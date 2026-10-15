"""Persist actual per-payment deductions and confirmed recipient remittances."""
from alembic import op
import sqlalchemy as sa
revision = '13d4a7b8c030'
down_revision = '13d4a7b8c029'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('payroll_advances', sa.Column('withheld_amount',sa.Numeric(18,2),nullable=False,server_default='0'))
    op.create_check_constraint('ck_payroll_advance_withheld','payroll_advances',"withheld_amount >= 0 AND withheld_amount <> 'NaN'::numeric")
    op.add_column('payroll_disbursements', sa.Column('withheld_amount',sa.Numeric(18,2),nullable=False,server_default='0'))
    op.create_check_constraint('ck_payroll_disbursement_withheld','payroll_disbursements',"withheld_amount >= 0 AND withheld_amount <> 'NaN'::numeric")
    op.drop_constraint('ck_payroll_advances_paid_gte_minimum','payroll_advances',type_='check')
    op.create_check_constraint('ck_payroll_advances_paid_gte_minimum','payroll_advances','paid_amount + withheld_amount >= minimum_due_amount')
    op.drop_constraint('ck_payroll_advances_paid_lte_calculated','payroll_advances',type_='check')
    op.create_check_constraint('ck_payroll_advances_paid_lte_calculated','payroll_advances','paid_amount + withheld_amount <= calculated_amount')
    op.execute("""
CREATE TABLE payroll_deduction_assessments (
	id SERIAL NOT NULL,
	company_id INTEGER NOT NULL,
	instruction_id INTEGER NOT NULL,
	beneficiary_id INTEGER NOT NULL,
	rule_approval_id INTEGER NOT NULL,
	reference VARCHAR(500) NOT NULL,
	request_key VARCHAR(100) NOT NULL,
	amount NUMERIC(18, 2) NOT NULL,
	priority INTEGER NOT NULL,
	priority_reference VARCHAR(500) NOT NULL,
	individual_limit_percentage NUMERIC(7, 4) NOT NULL,
	effective_from DATE NOT NULL,
	created_by INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_pda_company UNIQUE (company_id, id),
	CONSTRAINT uq_pda_request UNIQUE (company_id, request_key),
	CONSTRAINT uq_pda_reference UNIQUE (company_id, instruction_id, reference),
	CONSTRAINT fk_pda_instruction FOREIGN KEY(company_id, instruction_id) REFERENCES payroll_deduction_instructions (company_id, id) ON DELETE RESTRICT,
	CONSTRAINT fk_pda_beneficiary FOREIGN KEY(company_id, beneficiary_id) REFERENCES payroll_deduction_beneficiaries (company_id, id) ON DELETE RESTRICT,
	CONSTRAINT fk_pda_rule FOREIGN KEY(company_id, rule_approval_id) REFERENCES payroll_deduction_rule_approvals (company_id, id) ON DELETE RESTRICT,
	CONSTRAINT ck_pda_amount CHECK (amount > 0 AND amount <> 'NaN'::numeric),
	CONSTRAINT ck_pda_limits CHECK (priority >= 0 AND individual_limit_percentage > 0 AND individual_limit_percentage <= 70),
	CONSTRAINT ck_pda_references CHECK (length(trim(reference)) > 0 AND length(trim(request_key)) > 0 AND length(trim(priority_reference)) > 0),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
)

""")
    op.execute("""
CREATE TABLE payroll_withholding_batches (
	id SERIAL NOT NULL,
	company_id INTEGER NOT NULL,
	advance_id INTEGER,
	disbursement_id INTEGER,
	employee_id INTEGER NOT NULL,
	payment_date DATE NOT NULL,
	net_before NUMERIC(18, 2) NOT NULL,
	protected_amount NUMERIC(18, 2) NOT NULL,
	withheld_amount NUMERIC(18, 2) NOT NULL,
	income_reference VARCHAR(500) NOT NULL,
	request_key VARCHAR(100) NOT NULL,
	request_fingerprint VARCHAR(64) NOT NULL,
	created_by INTEGER NOT NULL,
	creation_xid BIGINT DEFAULT txid_current() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_pwb_company UNIQUE (company_id, id),
	CONSTRAINT uq_pwb_request UNIQUE (company_id, request_key),
	CONSTRAINT uq_pwb_advance UNIQUE (company_id, advance_id),
	CONSTRAINT uq_pwb_disbursement UNIQUE (company_id, disbursement_id),
	CONSTRAINT fk_pwb_advance FOREIGN KEY(company_id, advance_id) REFERENCES payroll_advances (company_id, id) ON DELETE RESTRICT,
	CONSTRAINT fk_pwb_disbursement FOREIGN KEY(company_id, disbursement_id) REFERENCES payroll_disbursements (company_id, id) ON DELETE RESTRICT,
	CONSTRAINT fk_pwb_employee FOREIGN KEY(company_id, employee_id) REFERENCES employees (company_id, id) ON DELETE RESTRICT,
	CONSTRAINT ck_pwb_source CHECK (num_nonnulls(advance_id,disbursement_id) = 1),
	CONSTRAINT ck_pwb_amounts CHECK (net_before > 0 AND net_before <> 'NaN'::numeric AND protected_amount >= 0 AND protected_amount <= net_before AND withheld_amount >= 0 AND withheld_amount <= net_before - protected_amount),
	CONSTRAINT ck_pwb_reference CHECK (length(trim(income_reference)) > 0 AND length(trim(request_key)) > 0),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
)

""")
    op.execute("""
CREATE TABLE payroll_withholding_lines (
	id SERIAL NOT NULL,
	company_id INTEGER NOT NULL,
	batch_id INTEGER NOT NULL,
	assessment_id INTEGER NOT NULL,
	requested_amount NUMERIC(18, 2) NOT NULL,
	amount NUMERIC(18, 2) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_pwl_company UNIQUE (company_id, id),
	CONSTRAINT uq_pwl_assessment UNIQUE (company_id, batch_id, assessment_id),
	CONSTRAINT fk_pwl_batch FOREIGN KEY(company_id, batch_id) REFERENCES payroll_withholding_batches (company_id, id) ON DELETE RESTRICT,
	CONSTRAINT fk_pwl_assessment FOREIGN KEY(company_id, assessment_id) REFERENCES payroll_deduction_assessments (company_id, id) ON DELETE RESTRICT,
	CONSTRAINT ck_pwl_amount CHECK (requested_amount >= 0 AND requested_amount <> 'NaN'::numeric AND amount >= 0 AND amount <= requested_amount)
)

""")
    op.execute("""
CREATE TABLE payroll_recipient_remittances (
	id SERIAL NOT NULL,
	company_id INTEGER NOT NULL,
	beneficiary_id INTEGER NOT NULL,
	journal_entry_id INTEGER NOT NULL,
	bank_statement_line_id INTEGER NOT NULL,
	amount NUMERIC(18, 2) NOT NULL,
	request_key VARCHAR(100) NOT NULL,
	request_fingerprint VARCHAR(64) NOT NULL,
	created_by INTEGER NOT NULL,
	creation_xid BIGINT DEFAULT txid_current() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_prr_company UNIQUE (company_id, id),
	CONSTRAINT uq_prr_request UNIQUE (company_id, request_key),
	CONSTRAINT uq_prr_journal UNIQUE (company_id, journal_entry_id),
	CONSTRAINT uq_prr_bank_line UNIQUE (company_id, bank_statement_line_id),
	CONSTRAINT fk_prr_beneficiary FOREIGN KEY(company_id, beneficiary_id) REFERENCES payroll_deduction_beneficiaries (company_id, id) ON DELETE RESTRICT,
	CONSTRAINT fk_prr_journal FOREIGN KEY(company_id, journal_entry_id) REFERENCES journal_entries (company_id, id) ON DELETE RESTRICT,
	CONSTRAINT fk_prr_bank_line FOREIGN KEY(company_id, bank_statement_line_id) REFERENCES bank_statement_lines (company_id, id) ON DELETE RESTRICT,
	CONSTRAINT ck_prr_amount CHECK (amount > 0 AND amount <> 'NaN'::numeric),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
)

""")
    op.execute("""
CREATE TABLE payroll_recipient_remittance_lines (
	id SERIAL NOT NULL,
	company_id INTEGER NOT NULL,
	remittance_id INTEGER NOT NULL,
	withholding_line_id INTEGER NOT NULL,
	amount NUMERIC(18, 2) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_prrl_source UNIQUE (company_id, remittance_id, withholding_line_id),
	CONSTRAINT fk_prrl_remittance FOREIGN KEY(company_id, remittance_id) REFERENCES payroll_recipient_remittances (company_id, id) ON DELETE RESTRICT,
	CONSTRAINT fk_prrl_withholding FOREIGN KEY(company_id, withholding_line_id) REFERENCES payroll_withholding_lines (company_id, id) ON DELETE RESTRICT,
	CONSTRAINT ck_prrl_amount CHECK (amount > 0 AND amount <> 'NaN'::numeric)
)

""")
    op.add_column('payroll_payslip_lines',sa.Column('source_withholding_line_id',sa.Integer(),nullable=True))
    op.drop_constraint('ck_payroll_payslip_lines_source_by_kind','payroll_payslip_lines',type_='check')
    op.execute("""ALTER TABLE payroll_payslip_lines ADD CONSTRAINT ck_payroll_payslip_lines_source_by_kind CHECK ((
                line_kind = 'earning'
                AND source_payroll_calculation_line_id IS NOT NULL
                AND source_payroll_statutory_result_line_id IS NULL
                AND source_payroll_deduction_result_line_id IS NULL AND source_withholding_line_id IS NULL
            ) OR (
                line_kind IN ('employee_withholding','employer_contribution')
                AND source_payroll_calculation_line_id IS NULL
                AND source_payroll_statutory_result_line_id IS NOT NULL
                AND source_payroll_deduction_result_line_id IS NULL AND source_withholding_line_id IS NULL
            ) OR (
                line_kind = 'non_statutory_deduction'
                AND source_payroll_calculation_line_id IS NULL
                AND source_payroll_statutory_result_line_id IS NULL
                AND num_nonnulls(source_payroll_deduction_result_line_id, source_withholding_line_id) = 1
            ))""")
    op.execute("""ALTER TABLE payroll_payslip_lines ADD CONSTRAINT fk_payslip_withholding_line FOREIGN KEY(company_id, source_withholding_line_id) REFERENCES payroll_withholding_lines (company_id, id) ON DELETE RESTRICT""")
    op.execute("""
    CREATE FUNCTION payroll_guard_execution_record() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE parent_xid bigint;
    BEGIN
      IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'Payroll execution history is immutable'; END IF;
      PERFORM id FROM companies WHERE id=NEW.company_id FOR UPDATE;
      IF TG_TABLE_NAME = 'payroll_withholding_lines' THEN
        SELECT creation_xid INTO parent_xid FROM payroll_withholding_batches WHERE company_id=NEW.company_id AND id=NEW.batch_id;
      ELSIF TG_TABLE_NAME = 'payroll_recipient_remittance_lines' THEN
        SELECT creation_xid INTO parent_xid FROM payroll_recipient_remittances WHERE company_id=NEW.company_id AND id=NEW.remittance_id;
      ELSE RETURN NEW;
      END IF;
      IF parent_xid IS DISTINCT FROM txid_current() THEN RAISE EXCEPTION 'Cannot append to sealed execution record'; END IF;
      RETURN NEW;
    END; $$;
    """)
    op.execute('CREATE TRIGGER trg_pda_immutable BEFORE INSERT OR UPDATE OR DELETE ON payroll_deduction_assessments FOR EACH ROW EXECUTE FUNCTION payroll_guard_execution_record()')
    op.execute('CREATE TRIGGER trg_pda_truncate BEFORE TRUNCATE ON payroll_deduction_assessments FOR EACH STATEMENT EXECUTE FUNCTION payroll_guard_execution_record()')
    op.execute('CREATE TRIGGER trg_pwb_immutable BEFORE INSERT OR UPDATE OR DELETE ON payroll_withholding_batches FOR EACH ROW EXECUTE FUNCTION payroll_guard_execution_record()')
    op.execute('CREATE TRIGGER trg_pwb_truncate BEFORE TRUNCATE ON payroll_withholding_batches FOR EACH STATEMENT EXECUTE FUNCTION payroll_guard_execution_record()')
    op.execute('CREATE TRIGGER trg_pwl_immutable BEFORE INSERT OR UPDATE OR DELETE ON payroll_withholding_lines FOR EACH ROW EXECUTE FUNCTION payroll_guard_execution_record()')
    op.execute('CREATE TRIGGER trg_pwl_truncate BEFORE TRUNCATE ON payroll_withholding_lines FOR EACH STATEMENT EXECUTE FUNCTION payroll_guard_execution_record()')
    op.execute('CREATE TRIGGER trg_prr_immutable BEFORE INSERT OR UPDATE OR DELETE ON payroll_recipient_remittances FOR EACH ROW EXECUTE FUNCTION payroll_guard_execution_record()')
    op.execute('CREATE TRIGGER trg_prr_truncate BEFORE TRUNCATE ON payroll_recipient_remittances FOR EACH STATEMENT EXECUTE FUNCTION payroll_guard_execution_record()')
    op.execute('CREATE TRIGGER trg_prrl_immutable BEFORE INSERT OR UPDATE OR DELETE ON payroll_recipient_remittance_lines FOR EACH ROW EXECUTE FUNCTION payroll_guard_execution_record()')
    op.execute('CREATE TRIGGER trg_prrl_truncate BEFORE TRUNCATE ON payroll_recipient_remittance_lines FOR EACH STATEMENT EXECUTE FUNCTION payroll_guard_execution_record()')
    op.execute("""
    CREATE FUNCTION payroll_check_execution_totals() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE actual numeric; parent_amount numeric;
    BEGIN
      IF TG_TABLE_NAME = 'payroll_withholding_batches' THEN
        SELECT COALESCE(sum(amount),0) INTO actual FROM payroll_withholding_lines WHERE company_id=NEW.company_id AND batch_id=NEW.id;
        parent_amount := NEW.withheld_amount;
      ELSE
        SELECT COALESCE(sum(amount),0) INTO actual FROM payroll_recipient_remittance_lines WHERE company_id=NEW.company_id AND remittance_id=NEW.id;
        parent_amount := NEW.amount;
      END IF;
      IF actual IS DISTINCT FROM parent_amount THEN RAISE EXCEPTION 'Payroll execution amounts do not reconcile'; END IF;
      RETURN NULL;
    END; $$;
    """)
    op.execute('CREATE CONSTRAINT TRIGGER trg_pwb_totals AFTER INSERT ON payroll_withholding_batches DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION payroll_check_execution_totals()')
    op.execute('CREATE CONSTRAINT TRIGGER trg_prr_totals AFTER INSERT ON payroll_recipient_remittances DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION payroll_check_execution_totals()')
    op.execute("""
    CREATE FUNCTION payroll_guard_withheld_payment() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE sealed_xid bigint;
    BEGIN
      IF TG_TABLE_NAME = 'payroll_advances' THEN
        SELECT creation_xid INTO sealed_xid FROM payroll_withholding_batches WHERE company_id=OLD.company_id AND advance_id=OLD.id;
        IF sealed_xid IS NOT NULL AND sealed_xid <> txid_current() AND
          (to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD)) THEN RAISE EXCEPTION 'Withholding advance is immutable'; END IF;
      ELSE
        SELECT creation_xid INTO sealed_xid FROM payroll_withholding_batches WHERE company_id=OLD.company_id AND disbursement_id=OLD.id;
        IF sealed_xid IS NOT NULL AND sealed_xid <> txid_current() AND
          (to_jsonb(NEW) - ARRAY['confirmed_at','reversed_on','cancelled_at','cancelled_by']) IS DISTINCT FROM
          (to_jsonb(OLD) - ARRAY['confirmed_at','reversed_on','cancelled_at','cancelled_by']) THEN RAISE EXCEPTION 'Withholding payment is immutable'; END IF;
      END IF;
      RETURN NEW;
    END; $$;
    """)
    op.execute("""
    CREATE FUNCTION payroll_guard_recipient_bank_use() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      PERFORM id FROM bank_statement_lines WHERE company_id=NEW.company_id AND id=NEW.bank_statement_line_id FOR UPDATE;
      IF TG_TABLE_NAME = 'payroll_recipient_remittances' THEN
        IF EXISTS (SELECT 1 FROM bank_statement_reconciliation_active_links WHERE company_id=NEW.company_id AND bank_statement_line_id=NEW.bank_statement_line_id) THEN
          RAISE EXCEPTION 'Bank transaction already reconciled';
        END IF;
      ELSE
        IF EXISTS (SELECT 1 FROM payroll_recipient_remittances WHERE company_id=NEW.company_id AND bank_statement_line_id=NEW.bank_statement_line_id) THEN
          RAISE EXCEPTION 'Bank transaction already attributed to payroll recipient';
        END IF;
      END IF;
      RETURN NEW;
    END; $$;
    """)
    op.execute('CREATE TRIGGER trg_prr_bank_use BEFORE INSERT ON payroll_recipient_remittances FOR EACH ROW EXECUTE FUNCTION payroll_guard_recipient_bank_use()')
    op.execute('CREATE TRIGGER trg_bank_recipient_use BEFORE INSERT OR UPDATE ON bank_statement_reconciliation_active_links FOR EACH ROW EXECUTE FUNCTION payroll_guard_recipient_bank_use()')
    op.execute('CREATE TRIGGER trg_pa_withheld BEFORE UPDATE ON payroll_advances FOR EACH ROW EXECUTE FUNCTION payroll_guard_withheld_payment()')
    op.execute('CREATE TRIGGER trg_pd_withheld BEFORE UPDATE ON payroll_disbursements FOR EACH ROW EXECUTE FUNCTION payroll_guard_withheld_payment()')


def downgrade():
    op.execute('LOCK TABLE payroll_deduction_assessments IN ACCESS EXCLUSIVE MODE')
    op.execute('LOCK TABLE payroll_withholding_batches IN ACCESS EXCLUSIVE MODE')
    op.execute('LOCK TABLE payroll_withholding_lines IN ACCESS EXCLUSIVE MODE')
    op.execute('LOCK TABLE payroll_recipient_remittances IN ACCESS EXCLUSIVE MODE')
    op.execute('LOCK TABLE payroll_recipient_remittance_lines IN ACCESS EXCLUSIVE MODE')
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM payroll_deduction_assessments)')):
        raise RuntimeError('Cannot discard payroll execution history')
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM payroll_withholding_batches)')):
        raise RuntimeError('Cannot discard payroll execution history')
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM payroll_withholding_lines)')):
        raise RuntimeError('Cannot discard payroll execution history')
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM payroll_recipient_remittances)')):
        raise RuntimeError('Cannot discard payroll execution history')
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM payroll_recipient_remittance_lines)')):
        raise RuntimeError('Cannot discard payroll execution history')
    op.execute('DROP TRIGGER trg_pa_withheld ON payroll_advances')
    op.execute('DROP TRIGGER trg_pd_withheld ON payroll_disbursements')
    op.execute('DROP FUNCTION payroll_guard_withheld_payment()')
    op.drop_constraint('fk_payslip_withholding_line','payroll_payslip_lines',type_='foreignkey')
    op.drop_constraint('ck_payroll_payslip_lines_source_by_kind','payroll_payslip_lines',type_='check')
    op.drop_column('payroll_payslip_lines','source_withholding_line_id')
    op.create_check_constraint('ck_payroll_payslip_lines_source_by_kind','payroll_payslip_lines',"(\n                line_kind = 'earning'\n                AND source_payroll_calculation_line_id IS NOT NULL\n                AND source_payroll_statutory_result_line_id IS NULL\n                AND source_payroll_deduction_result_line_id IS NULL\n            ) OR (\n                line_kind IN ('employee_withholding','employer_contribution')\n                AND source_payroll_calculation_line_id IS NULL\n                AND source_payroll_statutory_result_line_id IS NOT NULL\n                AND source_payroll_deduction_result_line_id IS NULL\n            ) OR (\n                line_kind = 'non_statutory_deduction'\n                AND source_payroll_calculation_line_id IS NULL\n                AND source_payroll_statutory_result_line_id IS NULL\n                AND source_payroll_deduction_result_line_id IS NOT NULL\n            )")
    op.execute('DROP TRIGGER trg_bank_recipient_use ON bank_statement_reconciliation_active_links')
    op.execute('DROP TRIGGER trg_prr_bank_use ON payroll_recipient_remittances')
    op.execute('DROP FUNCTION payroll_guard_recipient_bank_use()')
    op.drop_table('payroll_recipient_remittance_lines')
    op.drop_table('payroll_recipient_remittances')
    op.drop_table('payroll_withholding_lines')
    op.drop_table('payroll_withholding_batches')
    op.drop_table('payroll_deduction_assessments')
    op.execute('DROP FUNCTION payroll_check_execution_totals()')
    op.execute('DROP FUNCTION payroll_guard_execution_record()')
    op.drop_constraint('ck_payroll_advances_paid_gte_minimum','payroll_advances',type_='check')
    op.create_check_constraint('ck_payroll_advances_paid_gte_minimum','payroll_advances','paid_amount >= minimum_due_amount')
    op.drop_constraint('ck_payroll_advances_paid_lte_calculated','payroll_advances',type_='check')
    op.create_check_constraint('ck_payroll_advances_paid_lte_calculated','payroll_advances','paid_amount <= calculated_amount')
    op.drop_constraint('ck_payroll_advance_withheld','payroll_advances',type_='check')
    op.drop_column('payroll_advances','withheld_amount')
    op.drop_constraint('ck_payroll_disbursement_withheld','payroll_disbursements',type_='check')
    op.drop_column('payroll_disbursements','withheld_amount')
