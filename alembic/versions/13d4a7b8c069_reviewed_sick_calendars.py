"""Reviewed medical calendars preserve original orders and require atomic evidence."""
from alembic import op
revision='13d4a7b8c069'
down_revision='13d4a7b8c068'
branch_labels=None
depends_on=None


def upgrade():
    op.execute('''CREATE OR REPLACE FUNCTION erp_attendance_correction_complete() RETURNS trigger LANGUAGE plpgsql AS $$
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
          OR NOT EXISTS (WITH RECURSIVE chain(id) AS (
             SELECT request.calculation_id UNION
             SELECT c.replacement_payroll_calculation_id FROM payroll_corrections c
             JOIN chain ON c.original_payroll_calculation_id=chain.id WHERE c.company_id=request.company_id)
             SELECT 1 FROM chain JOIN jsonb_array_elements_text(app.payroll_calculation_ids::jsonb) x ON x::integer=chain.id
             WHERE chain.id<>request.calculation_id) THEN
        RAISE EXCEPTION 'Attendance correction requires matching independent review and payroll revision';
      END IF;
      RETURN NEW;
    END $$''')
    op.execute("""CREATE FUNCTION erp_sick_calendar_review() RETURNS trigger LANGUAGE plpgsql AS $$
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
    op.execute('CREATE CONSTRAINT TRIGGER sick_calendar_review AFTER INSERT ON payroll_sick_leave_calculations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION erp_sick_calendar_review()')


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM payroll_sick_leave_calculations
        WHERE insurance_assessment->>'calendar_request_id' IS NOT NULL)
        THEN RAISE EXCEPTION 'Cannot remove medical calendar history'; END IF; END $$""")
    op.execute('DROP TRIGGER sick_calendar_review ON payroll_sick_leave_calculations')
    op.execute('DROP FUNCTION erp_sick_calendar_review()')
