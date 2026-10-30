"""Independent earning-date evidence for signed manual payroll adjustments."""
from alembic import op
revision='13d4a7b8c067'
down_revision='13d4a7b8c066'
branch_labels=None
depends_on=None


def upgrade():
    op.execute('\nCREATE TABLE payroll_income_date_requests (\n\tid SERIAL NOT NULL, \n\tcompany_id INTEGER NOT NULL, \n\tline_id INTEGER NOT NULL, \n\trequest_key VARCHAR(90) NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\tpayload JSON NOT NULL, \n\tcreated_by INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_income_date_request_company UNIQUE (company_id, id), \n\tCONSTRAINT uq_income_date_request_key UNIQUE (company_id, request_key), \n\tCONSTRAINT fk_income_date_request_line FOREIGN KEY(company_id, line_id) REFERENCES payroll_calculation_lines (company_id, id), \n\tFOREIGN KEY(company_id) REFERENCES companies (id), \n\tFOREIGN KEY(created_by) REFERENCES users (id)\n)\n\n')
    op.execute('\nCREATE TABLE payroll_income_date_approvals (\n\tid SERIAL NOT NULL, \n\tcompany_id INTEGER NOT NULL, \n\tline_id INTEGER NOT NULL, \n\trequest_id INTEGER NOT NULL, \n\treviewed_by INTEGER NOT NULL, \n\treviewed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_income_date_approved_line UNIQUE (company_id, line_id), \n\tCONSTRAINT uq_income_date_approved_request UNIQUE (company_id, request_id), \n\tCONSTRAINT fk_income_date_approval_request FOREIGN KEY(company_id, request_id) REFERENCES payroll_income_date_requests (company_id, id), \n\tCONSTRAINT fk_income_date_approval_line FOREIGN KEY(company_id, line_id) REFERENCES payroll_calculation_lines (company_id, id), \n\tFOREIGN KEY(company_id) REFERENCES companies (id), \n\tFOREIGN KEY(reviewed_by) REFERENCES users (id)\n)\n\n')
    for table in ('payroll_income_date_requests','payroll_income_date_approvals'):
        op.execute(f'CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION erp_sick_application_immutable()')
        op.execute(f'CREATE TRIGGER {table}_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION erp_sick_application_immutable()')
    op.drop_constraint('ck_psls_source_amount','payroll_statutory_line_sources',type_='check')
    op.create_check_constraint('ck_psls_source_amount','payroll_statutory_line_sources',"source_amount <> 'NaN'::numeric")
    op.execute("""CREATE FUNCTION erp_income_dates_review() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE request payroll_income_date_requests%ROWTYPE; line payroll_calculation_lines%ROWTYPE;
        period payroll_periods%ROWTYPE; total numeric; n integer; distinct_n integer;
    BEGIN
        SELECT * INTO request FROM payroll_income_date_requests WHERE company_id=NEW.company_id AND id=NEW.request_id;
        SELECT * INTO line FROM payroll_calculation_lines WHERE company_id=NEW.company_id AND id=NEW.line_id;
        SELECT p.* INTO period FROM payroll_periods p JOIN payroll_calculations c ON c.payroll_period_id=p.id
            WHERE c.company_id=NEW.company_id AND c.id=line.payroll_calculation_id;
        SELECT sum((x->>'amount')::numeric),count(*),count(DISTINCT x->>'income_date') INTO total,n,distinct_n
            FROM jsonb_array_elements(request.payload::jsonb->'dates') x;
        IF request.line_id IS DISTINCT FROM NEW.line_id OR request.created_by=NEW.reviewed_by
            OR line.line_type IS DISTINCT FROM 'manual_adjustment' OR total IS DISTINCT FROM line.amount
            OR n<1 OR n<>distinct_n OR EXISTS (SELECT 1 FROM jsonb_array_elements(request.payload::jsonb->'dates') x
                WHERE (x->>'income_date')::date NOT BETWEEN period.start_date AND period.end_date
                OR (x->>'amount')::numeric*line.amount<0) THEN
            RAISE EXCEPTION 'Income dates require matching source and independent review';
        END IF;
        RETURN NEW;
    END $$""")
    op.execute('CREATE TRIGGER income_date_review BEFORE INSERT ON payroll_income_date_approvals FOR EACH ROW EXECUTE FUNCTION erp_income_dates_review()')
    op.execute("""CREATE FUNCTION erp_signed_income_source() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.source_amount<0 AND NOT EXISTS (
            WITH RECURSIVE lineage AS (
                SELECT l.*, ARRAY[l.id] AS visited FROM payroll_calculation_lines l
                    WHERE l.company_id=NEW.company_id AND l.id=NEW.payroll_calculation_line_id
                    AND l.line_type='manual_adjustment' AND l.amount<0
                UNION ALL
                SELECT p.*, a.visited || p.id FROM lineage a
                JOIN payroll_corrections c ON c.company_id=a.company_id
                    AND c.replacement_payroll_calculation_id=a.payroll_calculation_id
                JOIN payroll_calculation_lines p ON p.company_id=a.company_id
                    AND p.payroll_calculation_id=c.original_payroll_calculation_id
                    AND p.line_type=a.line_type AND p.amount=a.amount
                    AND (SELECT count(*) FROM payroll_calculation_lines sibling
                         WHERE sibling.company_id=a.company_id AND sibling.payroll_calculation_id=p.payroll_calculation_id
                           AND sibling.line_type='manual_adjustment')=1
                    AND (SELECT count(*) FROM payroll_calculation_lines sibling
                         WHERE sibling.company_id=a.company_id AND sibling.payroll_calculation_id=a.payroll_calculation_id
                           AND sibling.line_type='manual_adjustment')=1
                    AND p.currency_code=a.currency_code AND p.quantity IS NOT DISTINCT FROM a.quantity
                    AND p.rate IS NOT DISTINCT FROM a.rate AND p.description IS NOT DISTINCT FROM a.description
                WHERE NOT p.id=ANY(a.visited)
            ) SELECT 1 FROM lineage l JOIN payroll_income_date_approvals a
                ON a.company_id=l.company_id AND a.line_id=l.id) THEN
            RAISE EXCEPTION 'Negative tax source requires approved manual income dates';
        END IF;
        RETURN NEW;
    END $$""")
    op.execute('CREATE TRIGGER signed_income_source BEFORE INSERT ON payroll_statutory_line_sources FOR EACH ROW EXECUTE FUNCTION erp_signed_income_source()')


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM payroll_income_date_requests)
        OR EXISTS(SELECT 1 FROM payroll_statutory_line_sources WHERE source_amount<0) THEN
        RAISE EXCEPTION 'Cannot remove reviewed income date history'; END IF; END $$""")
    op.execute('DROP TRIGGER signed_income_source ON payroll_statutory_line_sources')
    op.execute('DROP FUNCTION erp_signed_income_source()')
    op.drop_table('payroll_income_date_approvals')
    op.drop_table('payroll_income_date_requests')
    op.execute('DROP FUNCTION erp_income_dates_review()')
    op.drop_constraint('ck_psls_source_amount','payroll_statutory_line_sources',type_='check')
    op.create_check_constraint('ck_psls_source_amount','payroll_statutory_line_sources',"source_amount >= 0 AND source_amount <> 'NaN'::numeric")
