"""Version and independently verify deduction recipient details.

Frozen DDL; no runtime model imports.
"""
from alembic import op
import sqlalchemy as sa
revision = '13d4a7b8c029'
down_revision = '13d4a7b8c028'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE payroll_deduction_beneficiaries (
	id SERIAL NOT NULL,
	company_id INTEGER NOT NULL,
	instruction_id INTEGER NOT NULL,
	request_key VARCHAR(100) NOT NULL,
	recipient_name VARCHAR(255) NOT NULL,
	recipient_identifier VARCHAR(100) NOT NULL,
	iban VARCHAR(29),
	settlement_kind VARCHAR(30) NOT NULL,
	account_id INTEGER NOT NULL,
	document_reference VARCHAR(500) NOT NULL,
	valid_from DATE NOT NULL,
	valid_to DATE,
	status VARCHAR(20) DEFAULT 'pending' NOT NULL,
	created_by INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	verified_by INTEGER,
	verified_at TIMESTAMP WITH TIME ZONE,
	revoked_by INTEGER,
	revoked_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_pdb_company_id UNIQUE (company_id, id),
	CONSTRAINT uq_pdb_request UNIQUE (company_id, request_key),
	CONSTRAINT fk_pdb_instruction FOREIGN KEY(company_id, instruction_id) REFERENCES payroll_deduction_instructions (company_id, id) ON DELETE RESTRICT,
	CONSTRAINT fk_pdb_account FOREIGN KEY(company_id, account_id) REFERENCES accounts (company_id, id) ON DELETE RESTRICT,
	CONSTRAINT ck_pdb_kind CHECK (settlement_kind IN ('recipient_payment', 'employer_recovery')),
	CONSTRAINT ck_pdb_iban CHECK ((settlement_kind = 'recipient_payment' AND iban IS NOT NULL AND iban ~ '^UA[0-9]{27}$') OR (settlement_kind = 'employer_recovery' AND iban IS NULL)),
	CONSTRAINT ck_pdb_dates CHECK (valid_to IS NULL OR valid_to >= valid_from),
	CONSTRAINT ck_pdb_text CHECK (length(trim(request_key)) > 0 AND length(trim(recipient_name)) > 0 AND length(trim(recipient_identifier)) > 0 AND length(trim(document_reference)) > 0),
	CONSTRAINT ck_pdb_lifecycle CHECK ((status = 'pending' AND verified_by IS NULL AND verified_at IS NULL AND revoked_by IS NULL AND revoked_at IS NULL) OR (status = 'verified' AND verified_by IS NOT NULL AND verified_at IS NOT NULL AND revoked_by IS NULL AND revoked_at IS NULL) OR (status = 'revoked' AND verified_by IS NOT NULL AND verified_at IS NOT NULL AND revoked_by IS NOT NULL AND revoked_at IS NOT NULL AND revoked_at >= verified_at)),
	CONSTRAINT ck_pdb_four_eyes CHECK (verified_by IS NULL OR verified_by <> created_by),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT,
	FOREIGN KEY(verified_by) REFERENCES users (id) ON DELETE RESTRICT,
	FOREIGN KEY(revoked_by) REFERENCES users (id) ON DELETE RESTRICT
)

""")
    op.execute('CREATE INDEX ix_pdb_instruction_dates ON payroll_deduction_beneficiaries (company_id, instruction_id, valid_from)')
    op.execute("""
    CREATE FUNCTION payroll_guard_deduction_beneficiary() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP IN ('DELETE','TRUNCATE') THEN
        RAISE EXCEPTION 'Deduction recipient history cannot be deleted';
      END IF;
      PERFORM id FROM companies WHERE id = NEW.company_id FOR UPDATE;
      IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'pending' THEN RAISE EXCEPTION 'Create pending recipient before verification'; END IF;
        RETURN NEW;
      END IF;
      IF (to_jsonb(NEW) - ARRAY['status','verified_by','verified_at','revoked_by','revoked_at'])
          IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status','verified_by','verified_at','revoked_by','revoked_at']) THEN
        RAISE EXCEPTION 'Recipient details are immutable; create another version';
      END IF;
      IF OLD.status = 'pending' AND NEW.status = 'verified' THEN
        IF EXISTS (SELECT 1 FROM payroll_deduction_beneficiaries b
          WHERE b.company_id = NEW.company_id AND b.instruction_id = NEW.instruction_id
          AND b.id <> NEW.id AND b.status = 'verified'
          AND (b.valid_to IS NULL OR b.valid_to >= NEW.valid_from)
          AND (NEW.valid_to IS NULL OR b.valid_from <= NEW.valid_to)) THEN
          RAISE EXCEPTION 'Verified recipient validity overlaps another version';
        END IF;
        RETURN NEW;
      END IF;
      IF OLD.status = 'verified' AND NEW.status = 'revoked'
          AND NEW.verified_by = OLD.verified_by AND NEW.verified_at = OLD.verified_at THEN
        RETURN NEW;
      END IF;
      RAISE EXCEPTION 'Invalid deduction recipient lifecycle transition';
    END; $$;
    """)
    op.execute("""CREATE TRIGGER trg_pdb_write BEFORE INSERT OR UPDATE OR DELETE ON payroll_deduction_beneficiaries
      FOR EACH ROW EXECUTE FUNCTION payroll_guard_deduction_beneficiary();
    """)
    op.execute("""CREATE TRIGGER trg_pdb_truncate BEFORE TRUNCATE ON payroll_deduction_beneficiaries
      FOR EACH STATEMENT EXECUTE FUNCTION payroll_guard_deduction_beneficiary();
    """)


def downgrade():
    op.execute('LOCK TABLE payroll_deduction_beneficiaries IN ACCESS EXCLUSIVE MODE')
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM payroll_deduction_beneficiaries)')):
        raise RuntimeError('Cannot discard deduction recipient history')
    op.drop_table('payroll_deduction_beneficiaries')
    op.execute('DROP FUNCTION payroll_guard_deduction_beneficiary()')
