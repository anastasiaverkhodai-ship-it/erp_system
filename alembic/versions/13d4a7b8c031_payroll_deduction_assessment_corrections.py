"""Preserve dated corrections of unused deduction assessments."""
from alembic import op
revision = '13d4a7b8c031'
down_revision = '13d4a7b8c030'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE payroll_payslips ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
    op.drop_constraint('uq_payroll_payslips_company_calculation','payroll_payslips',type_='unique')
    op.create_unique_constraint('uq_payroll_payslips_company_calculation_revision','payroll_payslips',['company_id','payroll_calculation_id','revision'])
    op.execute("ALTER TABLE payroll_recipient_remittances ADD COLUMN payment_beneficiary_id INTEGER")
    op.execute("ALTER TABLE payroll_recipient_remittances ADD CONSTRAINT fk_prr_payment_beneficiary FOREIGN KEY(company_id,payment_beneficiary_id) REFERENCES payroll_deduction_beneficiaries(company_id,id) ON DELETE RESTRICT")
    op.execute('\nCREATE TABLE payroll_deduction_assessment_closures (\n\tid SERIAL NOT NULL, \n\tcompany_id INTEGER NOT NULL, \n\tassessment_id INTEGER NOT NULL, \n\teffective_from DATE NOT NULL, \n\treason VARCHAR(500) NOT NULL, \n\trequest_key VARCHAR(100) NOT NULL, \n\trequest_fingerprint VARCHAR(64) NOT NULL, \n\treplacement_assessment_id INTEGER, \n\tcreated_by INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_pdac_assessment UNIQUE (company_id, assessment_id), \n\tCONSTRAINT uq_pdac_request UNIQUE (company_id, request_key), \n\tCONSTRAINT fk_pdac_assessment FOREIGN KEY(company_id, assessment_id) REFERENCES payroll_deduction_assessments (company_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_pdac_replacement FOREIGN KEY(company_id, replacement_assessment_id) REFERENCES payroll_deduction_assessments (company_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_pdac_reason CHECK (length(trim(reason)) > 0 AND length(trim(request_key)) > 0), \n\tCONSTRAINT ck_pdac_replacement CHECK (replacement_assessment_id IS NULL OR replacement_assessment_id <> assessment_id), \n\tFOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT\n)\n\n')
    op.execute("CREATE TRIGGER trg_pdac_immutable BEFORE INSERT OR UPDATE OR DELETE ON payroll_deduction_assessment_closures FOR EACH ROW EXECUTE FUNCTION payroll_guard_execution_record()")
    op.execute("CREATE TRIGGER trg_pdac_truncate BEFORE TRUNCATE ON payroll_deduction_assessment_closures FOR EACH STATEMENT EXECUTE FUNCTION payroll_guard_execution_record()")


def downgrade():
    op.execute("LOCK TABLE payroll_payslips IN ACCESS EXCLUSIVE MODE")
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM payroll_payslips WHERE revision<>1) THEN RAISE EXCEPTION 'Cannot discard payslip revision history'; END IF; END $$")
    op.drop_constraint('uq_payroll_payslips_company_calculation_revision','payroll_payslips',type_='unique')
    op.create_unique_constraint('uq_payroll_payslips_company_calculation','payroll_payslips',['company_id','payroll_calculation_id'])
    op.drop_column('payroll_payslips','revision')
    op.execute("LOCK TABLE payroll_recipient_remittances IN ACCESS EXCLUSIVE MODE")
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM payroll_recipient_remittances WHERE payment_beneficiary_id IS NOT NULL) THEN RAISE EXCEPTION 'Cannot discard recipient payment evidence'; END IF; END $$")
    op.drop_constraint('fk_prr_payment_beneficiary','payroll_recipient_remittances',type_='foreignkey')
    op.drop_column('payroll_recipient_remittances','payment_beneficiary_id')
    op.execute("LOCK TABLE payroll_deduction_assessment_closures IN ACCESS EXCLUSIVE MODE")
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM payroll_deduction_assessment_closures) THEN RAISE EXCEPTION 'Cannot discard deduction correction history'; END IF; END $$")
    op.drop_table('payroll_deduction_assessment_closures')
