"""Persistent payroll bank batches and immutable allocations.

Revision ID: p13bankbatch261116
Revises: p13paidacl261116
"""
import re
from alembic import op
import sqlalchemy as sa
revision = "p13bankbatch261116"
down_revision = "p13paidacl261116"
branch_labels = None
depends_on = None

TABLES = (
    ("bank_statement_reconciliations", "ck_bank_reconciliation_target"),
    ("bank_statement_reconciliation_active_links", "ck_bank_reconciliation_link_target"),
)


def _execute(sql):
    for statement in re.split(r";\s*(?=CREATE |GRANT )", sql):
        if statement.strip():
            op.execute(statement)


def upgrade():
    _execute("""
CREATE TABLE payroll_bank_batches (
	id SERIAL NOT NULL,
	company_id INTEGER NOT NULL,
	employee_id INTEGER NOT NULL,
	bank_statement_line_id INTEGER NOT NULL,
	request_key VARCHAR(100) NOT NULL,
	amount NUMERIC(18, 2) NOT NULL,
	currency_code VARCHAR(3) NOT NULL,
	created_by INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_payroll_bank_batch_company_id UNIQUE (company_id, id),
	CONSTRAINT uq_payroll_bank_batch_request UNIQUE (company_id, request_key),
	FOREIGN KEY(company_id, bank_statement_line_id) REFERENCES bank_statement_lines (company_id, id) ON DELETE RESTRICT,
	FOREIGN KEY(company_id, employee_id) REFERENCES employees (company_id, id) ON DELETE RESTRICT,
	CONSTRAINT ck_payroll_bank_batch_amount CHECK (amount > 0 AND currency_code = 'UAH'),
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE RESTRICT,
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
)

;

CREATE TABLE payroll_bank_batch_allocations (
	id SERIAL NOT NULL,
	company_id INTEGER NOT NULL,
	batch_id INTEGER NOT NULL,
	payroll_disbursement_id INTEGER NOT NULL,
	journal_entry_id INTEGER NOT NULL,
	amount NUMERIC(18, 2) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_payroll_bank_batch_member UNIQUE (company_id, batch_id, payroll_disbursement_id),
	FOREIGN KEY(company_id, batch_id) REFERENCES payroll_bank_batches (company_id, id) ON DELETE RESTRICT,
	FOREIGN KEY(company_id, payroll_disbursement_id) REFERENCES payroll_disbursements (company_id, id) ON DELETE RESTRICT,
	FOREIGN KEY(company_id, journal_entry_id) REFERENCES journal_entries (company_id, id) ON DELETE RESTRICT,
	CONSTRAINT ck_payroll_bank_batch_allocation_amount CHECK (amount > 0)
)

;""")
    for table, check in TABLES:
        op.add_column(table, sa.Column("payroll_bank_batch_id", sa.Integer(), nullable=True))
        op.create_foreign_key(None, table, "payroll_bank_batches",
            ["company_id", "payroll_bank_batch_id"], ["company_id", "id"], ondelete="RESTRICT")
        op.drop_constraint(check, table, type_="check")
        op.create_check_constraint(check, table,
            "num_nonnulls(payment_id, payroll_disbursement_id, payroll_advance_id, payroll_bank_batch_id) = 1")
    _execute("""
        CREATE FUNCTION public.payroll_bank_batch_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'Payroll bank batch history is immutable';
        END $$;
        CREATE TRIGGER payroll_bank_batch_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
            ON public.payroll_bank_batches FOR EACH STATEMENT
            EXECUTE FUNCTION public.payroll_bank_batch_immutable();
        CREATE TRIGGER payroll_bank_allocation_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
            ON public.payroll_bank_batch_allocations FOR EACH STATEMENT
            EXECUTE FUNCTION public.payroll_bank_batch_immutable();
        CREATE FUNCTION public.payroll_bank_allocation_sealed() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.bank_statement_reconciliations
                       WHERE company_id = NEW.company_id AND payroll_bank_batch_id = NEW.batch_id) THEN
                RAISE EXCEPTION 'Posted batch allocations are sealed';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER payroll_bank_allocation_sealed BEFORE INSERT
            ON public.payroll_bank_batch_allocations FOR EACH ROW
            EXECUTE FUNCTION public.payroll_bank_allocation_sealed();
        CREATE FUNCTION public.payroll_bank_batch_event_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE b public.payroll_bank_batches; original public.bank_statement_reconciliations;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                IF OLD.payroll_bank_batch_id IS NOT NULL OR
                   (TG_OP = 'UPDATE' AND NEW.payroll_bank_batch_id IS NOT NULL) THEN
                    RAISE EXCEPTION 'Bank batch reconciliation history is immutable';
                END IF;
                IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
                RETURN NEW;
            END IF;
            IF NEW.payroll_bank_batch_id IS NULL THEN RETURN NEW; END IF;
            SELECT * INTO STRICT b FROM public.payroll_bank_batches
                WHERE company_id=NEW.company_id AND id=NEW.payroll_bank_batch_id;
            IF NEW.bank_statement_line_id <> b.bank_statement_line_id OR
               NEW.matched_amount <> b.amount OR NEW.currency_code <> b.currency_code THEN
                RAISE EXCEPTION 'Batch reconciliation source mismatch';
            END IF;
            IF NEW.reversal_of_id IS NOT NULL THEN
                SELECT * INTO STRICT original FROM public.bank_statement_reconciliations
                    WHERE company_id=NEW.company_id AND id=NEW.reversal_of_id;
                IF original.payroll_bank_batch_id IS DISTINCT FROM b.id OR original.reversal_of_id IS NOT NULL THEN
                    RAISE EXCEPTION 'Invalid batch reversal source';
                END IF;
                RETURN NEW;
            END IF;
            IF (SELECT count(*) FROM public.payroll_bank_batch_allocations WHERE company_id=b.company_id AND batch_id=b.id) < 2
                OR (SELECT sum(amount) FROM public.payroll_bank_batch_allocations WHERE company_id=b.company_id AND batch_id=b.id) <> b.amount THEN
                RAISE EXCEPTION 'Batch allocation count or total mismatch';
            END IF;
            IF EXISTS (SELECT 1 FROM public.bank_statement_reconciliations
                       WHERE company_id=b.company_id AND payroll_bank_batch_id=b.id AND reversal_of_id IS NULL) THEN
                RAISE EXCEPTION 'Batch already posted';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER payroll_bank_batch_event_guard BEFORE INSERT OR UPDATE OR DELETE
            ON public.bank_statement_reconciliations FOR EACH ROW
            EXECUTE FUNCTION public.payroll_bank_batch_event_guard();
        GRANT SELECT, INSERT ON public.payroll_bank_batches, public.payroll_bank_batch_allocations TO erp_user;
        GRANT USAGE, SELECT ON SEQUENCE public.payroll_bank_batches_id_seq, public.payroll_bank_batch_allocations_id_seq TO erp_user;
    """)


def downgrade():
    _execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM public.payroll_bank_batches) THEN
            RAISE EXCEPTION 'Bank batch history must be preserved';
        END IF;
    END $$""")
    op.execute("DROP TRIGGER payroll_bank_batch_event_guard ON public.bank_statement_reconciliations")
    op.execute("DROP FUNCTION public.payroll_bank_batch_event_guard()")
    for table, check in reversed(TABLES):
        op.drop_constraint(check, table, type_="check")
        op.drop_column(table, "payroll_bank_batch_id")
        op.create_check_constraint(check, table,
            "num_nonnulls(payment_id, payroll_disbursement_id, payroll_advance_id) = 1")
    op.drop_table("payroll_bank_batch_allocations")
    op.drop_table("payroll_bank_batches")
    _execute("DROP FUNCTION public.payroll_bank_allocation_sealed()")
    _execute("DROP FUNCTION public.payroll_bank_batch_immutable()")
