"""Require independently reviewed, fully posted postpayment severance revisions."""
from alembic import op
revision='p13exec261103'
down_revision='p13sev261103d'
branch_labels=None
depends_on=None


def upgrade():
    op.drop_constraint('ck_payroll_correction_source_kind','payroll_sick_correction_applications',type_='check')
    op.create_check_constraint('ck_payroll_correction_source_kind','payroll_sick_correction_applications',
        "(source_kind='sick' AND original_sick_id IS NOT NULL AND replacement_sick_id IS NOT NULL) OR (source_kind IN ('leave','attendance','severance') AND original_sick_id IS NULL AND replacement_sick_id IS NULL)")
    op.execute("""CREATE FUNCTION erp_severance_execution_complete() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.source_kind<>'severance' THEN RETURN NEW; END IF;
      IF NOT EXISTS (
        SELECT 1 FROM payroll_severance_financial_proposals p
        JOIN payroll_severance_correction_cases c ON c.company_id=p.company_id AND c.id=p.correction_case_id
        JOIN payroll_severance_independent_reviews r ON r.company_id=p.company_id AND r.financial_proposal_id=p.id
        JOIN payroll_corrections link ON link.company_id=c.company_id AND link.original_payroll_calculation_id=c.original_payroll_calculation_id
        JOIN payroll_calculation_lines line ON line.company_id=link.company_id AND line.payroll_calculation_id=link.replacement_payroll_calculation_id
        JOIN payroll_termination_severances source ON source.company_id=line.company_id AND source.id=line.source_termination_severance_id
        WHERE p.company_id=NEW.company_id AND NEW.request_key='severance-proposal:'||p.id::text
          AND NEW.request_fingerprint=p.request_fingerprint AND NEW.created_by=r.reviewer_id
          AND r.decision='reviewed_pending_statutory' AND r.reviewer_id<>p.submitted_by AND r.reviewer_id<>c.created_by
          AND NEW.correction_date>=r.review_date AND NEW.correction_date>=c.as_of_date
          AND source.supersedes_id=c.original_severance_source_id
          AND line.amount=source.gross_amount AND line.amount=(p.proposed_financial->>'gross')::numeric
          AND EXISTS(SELECT 1 FROM jsonb_array_elements_text(NEW.payroll_calculation_ids::jsonb) member
              WHERE member::integer=link.replacement_payroll_calculation_id)) THEN
          RAISE EXCEPTION 'Severance execution requires matching independent review and complete financial revision';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute('CREATE CONSTRAINT TRIGGER severance_execution_complete AFTER INSERT ON payroll_sick_correction_applications DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION erp_severance_execution_complete()')
    op.execute("""CREATE FUNCTION erp_paid_severance_revision_complete() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.supersedes_id IS NULL THEN RETURN NEW; END IF;
      IF EXISTS (SELECT 1 FROM payroll_calculation_lines line
        JOIN payroll_disbursements payment ON payment.company_id=line.company_id AND payment.payroll_calculation_id=line.payroll_calculation_id
        JOIN journal_entries journal ON journal.company_id=payment.company_id AND journal.payroll_disbursement_id=payment.id
        WHERE line.company_id=NEW.company_id AND line.source_termination_severance_id=NEW.supersedes_id
          AND journal.status='posted' AND journal.reversal_of_id IS NULL)
        AND NOT EXISTS (SELECT 1 FROM payroll_sick_correction_applications a
          JOIN payroll_calculation_lines line ON line.company_id=a.company_id AND line.source_termination_severance_id=NEW.id
          WHERE a.company_id=NEW.company_id AND a.source_kind='severance'
            AND EXISTS(SELECT 1 FROM jsonb_array_elements_text(a.payroll_calculation_ids::jsonb) member
              WHERE member::integer=line.payroll_calculation_id)) THEN
        RAISE EXCEPTION 'Paid severance source requires an atomic financial correction';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute('CREATE CONSTRAINT TRIGGER paid_severance_revision_complete AFTER INSERT ON payroll_termination_severances DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION erp_paid_severance_revision_complete()')


    op.execute("""CREATE OR REPLACE FUNCTION protect_severance_adjustment_journal()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.status::text='posted' AND EXISTS (
        SELECT 1 FROM payroll_severance_adjustment_journals s
        WHERE s.company_id=NEW.company_id AND s.journal_entry_id=NEW.id)
      AND NOT EXISTS (
        SELECT 1 FROM payroll_severance_adjustment_journals s
        JOIN payroll_sick_correction_applications a ON a.company_id=s.company_id
          AND a.source_kind='severance' AND a.request_key='severance-proposal:'||s.financial_proposal_id::text
        JOIN payroll_sick_payroll_revisions v ON v.company_id=a.company_id AND v.application_id=a.id
        JOIN payroll_severance_independent_reviews r ON r.company_id=s.company_id
          AND r.financial_proposal_id=s.financial_proposal_id
        WHERE s.company_id=NEW.company_id AND s.journal_entry_id=NEW.id
          AND v.journal_entry_id=NEW.id AND v.replacement_calculation_id=NEW.payroll_calculation_id
          AND r.decision='reviewed_pending_statutory' AND r.reviewer_id=a.created_by
          AND a.correction_date=NEW.entry_date)
      THEN RAISE EXCEPTION 'Severance adjustment requires verified approval'; END IF;
      RETURN NEW;
    END $$""")

    op.execute("""CREATE OR REPLACE FUNCTION erp_sick_calendar_review() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE request payroll_attendance_correction_requests%ROWTYPE;
            proof payroll_attendance_corrections%ROWTYPE;
            source payroll_sick_leave_calculations%ROWTYPE;
            requested_id integer;
    BEGIN
      requested_id=(NEW.insurance_assessment->>'calendar_request_id')::integer;
      IF requested_id IS NULL THEN
        IF NEW.supersedes_id IS NOT NULL AND EXISTS (
          SELECT 1 FROM payroll_sick_leave_calculations previous JOIN leave_requests leave_order
            ON leave_order.id=previous.leave_request_id AND leave_order.company_id=previous.company_id
          WHERE previous.company_id=NEW.company_id AND previous.id=NEW.supersedes_id
          AND (COALESCE(NEW.insurance_assessment->'certificate_dates'->>'start',leave_order.start_date::text)
                  IS DISTINCT FROM COALESCE(previous.insurance_assessment->'certificate_dates'->>'start',leave_order.start_date::text)
            OR COALESCE(NEW.insurance_assessment->'certificate_dates'->>'end',leave_order.end_date::text)
                  IS DISTINCT FROM COALESCE(previous.insurance_assessment->'certificate_dates'->>'end',leave_order.end_date::text)))
          THEN RAISE EXCEPTION 'Changed medical dates require independent calendar review'; END IF;
        RETURN NEW;
      END IF;
      SELECT * INTO request FROM payroll_attendance_correction_requests WHERE company_id=NEW.company_id AND id=requested_id;
      SELECT * INTO proof FROM payroll_attendance_corrections WHERE company_id=NEW.company_id AND request_id=requested_id;
      SELECT * INTO source FROM payroll_sick_leave_calculations WHERE company_id=NEW.company_id AND id=NEW.supersedes_id;
      IF request.id IS NULL OR proof.id IS NULL OR source.id IS NULL
         OR proof.reviewed_by IS DISTINCT FROM NEW.calculated_by OR request.created_by=NEW.calculated_by
         OR NEW.insurance_evidence_id IS DISTINCT FROM (request.payload->'sick_calendar'->>'insurance_evidence_id')::integer
         OR NOT EXISTS(SELECT 1 FROM payroll_calculations p
             JOIN employment_contracts requested_contract ON requested_contract.company_id=p.company_id
                AND requested_contract.id=p.employment_contract_id
             JOIN employment_contracts affected_contract ON affected_contract.company_id=p.company_id
                AND affected_contract.id=NEW.employment_contract_id
                AND affected_contract.employee_id=requested_contract.employee_id
             JOIN payroll_sick_leave_calculations requested_source ON requested_source.company_id=p.company_id
                AND requested_source.id=(request.payload->'sick_calendar'->>'original_sick_id')::integer
                AND requested_source.employment_contract_id=requested_contract.id
             JOIN payroll_insurance_evidence requested_evidence ON requested_evidence.company_id=p.company_id
                AND requested_evidence.id=requested_source.insurance_evidence_id
             JOIN payroll_insurance_evidence affected_evidence ON affected_evidence.company_id=p.company_id
                AND affected_evidence.id=source.insurance_evidence_id
                AND affected_evidence.case_start=requested_evidence.case_start
                AND affected_evidence.employee_id=requested_evidence.employee_id
             WHERE p.id=request.calculation_id AND p.company_id=NEW.company_id)
         OR (source.id=(request.payload->'sick_calendar'->>'original_sick_id')::integer AND
            ((NEW.insurance_assessment->'certificate_dates'->>'start') IS DISTINCT FROM (request.payload->'sick_calendar'->>'start_date')
             OR (NEW.insurance_assessment->'certificate_dates'->>'end') IS DISTINCT FROM (request.payload->'sick_calendar'->>'end_date')))
      THEN RAISE EXCEPTION 'Medical calendar requires completed independent financial review'; END IF;
      RETURN NEW;
    END $$""")

def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM payroll_sick_correction_applications WHERE source_kind='severance')
        THEN RAISE EXCEPTION 'Cannot remove completed severance correction history'; END IF; END $$""")
    op.execute("""CREATE OR REPLACE FUNCTION protect_severance_adjustment_journal()
    RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF NEW.status::text='posted' AND EXISTS (
        SELECT 1 FROM payroll_severance_adjustment_journals s
        WHERE s.company_id=NEW.company_id AND s.journal_entry_id=NEW.id)
      THEN RAISE EXCEPTION 'Severance adjustment requires verified approval'; END IF;
      RETURN NEW;
    END $$""")
    op.execute("""CREATE OR REPLACE FUNCTION erp_sick_calendar_review() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE request payroll_attendance_correction_requests%ROWTYPE;
            proof payroll_attendance_corrections%ROWTYPE;
            source payroll_sick_leave_calculations%ROWTYPE;
            requested_id integer;
    BEGIN
      requested_id=(NEW.insurance_assessment->>'calendar_request_id')::integer;
      IF requested_id IS NULL THEN
        IF NEW.supersedes_id IS NOT NULL AND EXISTS (
          SELECT 1 FROM payroll_sick_leave_calculations previous JOIN leave_requests leave_order
            ON leave_order.id=previous.leave_request_id AND leave_order.company_id=previous.company_id
          WHERE previous.company_id=NEW.company_id AND previous.id=NEW.supersedes_id
          AND (COALESCE(NEW.insurance_assessment->'certificate_dates'->>'start',leave_order.start_date::text)
                  IS DISTINCT FROM COALESCE(previous.insurance_assessment->'certificate_dates'->>'start',leave_order.start_date::text)
            OR COALESCE(NEW.insurance_assessment->'certificate_dates'->>'end',leave_order.end_date::text)
                  IS DISTINCT FROM COALESCE(previous.insurance_assessment->'certificate_dates'->>'end',leave_order.end_date::text)))
          THEN RAISE EXCEPTION 'Changed medical dates require independent calendar review'; END IF;
        RETURN NEW;
      END IF;
      SELECT * INTO request FROM payroll_attendance_correction_requests WHERE company_id=NEW.company_id AND id=requested_id;
      SELECT * INTO proof FROM payroll_attendance_corrections WHERE company_id=NEW.company_id AND request_id=requested_id;
      SELECT * INTO source FROM payroll_sick_leave_calculations WHERE company_id=NEW.company_id AND id=NEW.supersedes_id;
      IF request.id IS NULL OR proof.id IS NULL OR source.id IS NULL
         OR proof.reviewed_by IS DISTINCT FROM NEW.calculated_by OR request.created_by=NEW.calculated_by
         OR NEW.insurance_evidence_id IS DISTINCT FROM (request.payload->'sick_calendar'->>'insurance_evidence_id')::integer
         OR NOT EXISTS(SELECT 1 FROM payroll_calculations p WHERE p.id=request.calculation_id
             AND p.company_id=NEW.company_id AND p.employment_contract_id=NEW.employment_contract_id)
         OR (source.id=(request.payload->'sick_calendar'->>'original_sick_id')::integer AND
            ((NEW.insurance_assessment->'certificate_dates'->>'start') IS DISTINCT FROM (request.payload->'sick_calendar'->>'start_date')
             OR (NEW.insurance_assessment->'certificate_dates'->>'end') IS DISTINCT FROM (request.payload->'sick_calendar'->>'end_date')))
      THEN RAISE EXCEPTION 'Medical calendar requires completed independent financial review'; END IF;
      RETURN NEW;
    END $$""")
    op.execute('DROP TRIGGER paid_severance_revision_complete ON payroll_termination_severances')
    op.execute('DROP FUNCTION erp_paid_severance_revision_complete()')
    op.execute('DROP TRIGGER severance_execution_complete ON payroll_sick_correction_applications')
    op.execute('DROP FUNCTION erp_severance_execution_complete()')
    op.drop_constraint('ck_payroll_correction_source_kind','payroll_sick_correction_applications',type_='check')
    op.create_check_constraint('ck_payroll_correction_source_kind','payroll_sick_correction_applications',
        "(source_kind='sick' AND original_sick_id IS NOT NULL AND replacement_sick_id IS NOT NULL) OR (source_kind IN ('leave','attendance') AND original_sick_id IS NULL AND replacement_sick_id IS NULL)")
