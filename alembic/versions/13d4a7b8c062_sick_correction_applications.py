"""Sick correction application and signed payroll revision ledger.

Revision ID: 13d4a7b8c062
Revises: 13d4a7b8c061
"""
from alembic import op
import sqlalchemy as sa

revision = '13d4a7b8c062'
down_revision = '13d4a7b8c061'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint('ck_sick_correction_pending', 'payroll_sick_corrections', type_='check')
    op.create_check_constraint('ck_sick_correction_pending', 'payroll_sick_corrections', "status IN ('pending_review', 'applied')")
    op.create_table('payroll_sick_correction_applications',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('company_id',sa.Integer(),nullable=False),
        sa.Column('original_sick_id',sa.Integer(),nullable=False),
        sa.Column('replacement_sick_id',sa.Integer(),nullable=False),
        sa.Column('payroll_calculation_ids',sa.JSON(),nullable=False,server_default=sa.text("'[]'::json")),
        sa.Column('source_replacements',sa.JSON(),nullable=False,server_default=sa.text("'{}'::json")),
        sa.Column('request_key',sa.String(100),nullable=False),
        sa.Column('request_fingerprint',sa.String(64),nullable=False),
        sa.Column('reason',sa.Text(),nullable=False),
        sa.Column('correction_date',sa.Date(),nullable=False),
        sa.Column('created_by',sa.Integer(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.UniqueConstraint('company_id','id',name='uq_sick_application_company_id'),
        sa.UniqueConstraint('company_id','request_key',name='uq_sick_application_request'),
        sa.UniqueConstraint('company_id','original_sick_id',name='uq_sick_application_original'),
        sa.UniqueConstraint('company_id','replacement_sick_id',name='uq_sick_application_replacement'),
        sa.ForeignKeyConstraint(['company_id','original_sick_id'],['payroll_sick_leave_calculations.company_id','payroll_sick_leave_calculations.id'],name='fk_sick_application_original',ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['company_id','replacement_sick_id'],['payroll_sick_leave_calculations.company_id','payroll_sick_leave_calculations.id'],name='fk_sick_application_replacement',ondelete='RESTRICT'),
    )
    op.create_table('payroll_sick_payroll_revisions',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('company_id',sa.Integer(),nullable=False),
        sa.Column('application_id',sa.Integer(),nullable=False),
        sa.Column('original_calculation_id',sa.Integer(),nullable=False),
        sa.Column('replacement_calculation_id',sa.Integer(),nullable=False),
        sa.Column('journal_entry_id',sa.Integer(),nullable=True),
        sa.Column('original_financial',sa.JSON(),nullable=False),
        sa.Column('replacement_financial',sa.JSON(),nullable=False),
        sa.Column('net_delta',sa.Numeric(18,2),nullable=False),
        sa.UniqueConstraint('company_id','id',name='uq_sick_payroll_revision_company_id'),
        sa.UniqueConstraint('company_id','original_calculation_id',name='uq_sick_payroll_revision_original'),
        sa.UniqueConstraint('company_id','replacement_calculation_id',name='uq_sick_payroll_revision_replacement'),
        sa.UniqueConstraint('company_id','journal_entry_id',name='uq_sick_payroll_revision_journal'),
        sa.ForeignKeyConstraint(['company_id','application_id'],['payroll_sick_correction_applications.company_id','payroll_sick_correction_applications.id'],name='fk_sick_payroll_revision_application',ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['company_id','original_calculation_id'],['payroll_calculations.company_id','payroll_calculations.id'],name='fk_sick_payroll_revision_original',ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['company_id','replacement_calculation_id'],['payroll_calculations.company_id','payroll_calculations.id'],name='fk_sick_payroll_revision_replacement',ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['company_id','journal_entry_id'],['journal_entries.company_id','journal_entries.id'],name='fk_sick_payroll_revision_journal',ondelete='RESTRICT'),
    )
    op.execute('''CREATE FUNCTION erp_sick_application_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'Sick correction applications are immutable' USING ERRCODE='23514'; END $$''')
    for table in ('payroll_sick_correction_applications', 'payroll_sick_payroll_revisions'):
        op.execute(f'CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION erp_sick_application_immutable()')
        op.execute(f'CREATE TRIGGER trg_{table}_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION erp_sick_application_immutable()')


    op.execute("""CREATE FUNCTION erp_sick_request_transition() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP='INSERT' THEN
          IF NEW.status <> 'pending_review' THEN
            RAISE EXCEPTION 'New sick correction requests must await review' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END IF;
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Sick correction request history is immutable'; END IF;
        IF (to_jsonb(NEW)-'status') IS DISTINCT FROM (to_jsonb(OLD)-'status')
          OR OLD.status <> 'pending_review' OR NEW.status <> 'applied'
          OR NOT EXISTS (SELECT 1 FROM payroll_sick_correction_applications a
            JOIN payroll_sick_leave_calculations s ON s.company_id=a.company_id
              AND s.id::text=a.source_replacements->>NEW.original_calculation_id::text
            WHERE a.company_id=NEW.company_id
              AND s.insurance_evidence_id=NEW.insurance_evidence_id) THEN
          RAISE EXCEPTION 'Sick correction requires a completed matching application' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END $$""")
    op.execute('CREATE TRIGGER trg_sick_request_transition BEFORE INSERT OR UPDATE OR DELETE ON payroll_sick_corrections FOR EACH ROW EXECUTE FUNCTION erp_sick_request_transition()')
    op.execute('CREATE TRIGGER trg_sick_request_truncate BEFORE TRUNCATE ON payroll_sick_corrections FOR EACH STATEMENT EXECUTE FUNCTION erp_sick_application_immutable()')

    op.execute("""CREATE FUNCTION erp_sick_application_complete() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE app payroll_sick_correction_applications%ROWTYPE; cid integer; new_id integer; pair record;
    BEGIN
      IF TG_TABLE_NAME='payroll_sick_leave_calculations' THEN
        IF NEW.revision=1 THEN RETURN NEW; END IF;
        IF NOT EXISTS (SELECT 1 FROM payroll_sick_correction_applications a WHERE a.company_id=NEW.company_id
          AND a.source_replacements->>NEW.supersedes_id::text=NEW.id::text) THEN
          RAISE EXCEPTION 'Sick revision requires a complete correction application' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END IF;
      SELECT * INTO app FROM payroll_sick_correction_applications WHERE id=NEW.id;
      IF app.source_replacements->>app.original_sick_id::text IS DISTINCT FROM app.replacement_sick_id::text THEN
        RAISE EXCEPTION 'Sick application membership is incomplete' USING ERRCODE='23514';
      END IF;
      FOR pair IN SELECT * FROM json_each_text(app.source_replacements) LOOP
        IF NOT EXISTS (SELECT 1 FROM payroll_sick_leave_calculations s WHERE s.company_id=app.company_id
            AND s.id=pair.value::integer AND s.supersedes_id=pair.key::integer) THEN
          RAISE EXCEPTION 'Invalid sick source application lineage' USING ERRCODE='23514';
        END IF;
      END LOOP;
      IF jsonb_array_length(app.payroll_calculation_ids::jsonb)=0 AND EXISTS (
        SELECT 1 FROM payroll_calculation_lines l WHERE l.company_id=app.company_id
          AND app.source_replacements::jsonb ? l.source_sick_leave_calculation_id::text) THEN
        RAISE EXCEPTION 'Linked sick sources require a financial application' USING ERRCODE='23514';
      END IF;
      FOR new_id IN SELECT value::integer FROM json_array_elements_text(app.payroll_calculation_ids) LOOP
        IF NOT EXISTS (SELECT 1 FROM payroll_calculations c
            JOIN payroll_corrections link ON link.company_id=c.company_id AND link.replacement_payroll_calculation_id=c.id
            JOIN payroll_statutory_results t ON t.company_id=c.company_id AND t.payroll_calculation_id=c.id
            WHERE c.company_id=app.company_id AND c.id=new_id
              AND (EXISTS (SELECT 1 FROM journal_entries j WHERE j.company_id=c.company_id
                    AND j.payroll_calculation_id=c.id AND j.reversal_of_id IS NULL AND j.status='posted')
                OR EXISTS (SELECT 1 FROM payroll_sick_payroll_revisions r WHERE r.company_id=c.company_id
                    AND r.application_id=app.id AND r.replacement_calculation_id=c.id AND r.journal_entry_id IS NULL
                    AND r.original_financial::jsonb=r.replacement_financial::jsonb AND r.net_delta=0))) THEN
          RAISE EXCEPTION 'Sick payroll correction requires complete taxes and posted accounting' USING ERRCODE='23514';
        END IF;
      END LOOP;
      RETURN NEW;
    END $$""")
    op.execute('CREATE CONSTRAINT TRIGGER trg_sick_application_complete AFTER INSERT ON payroll_sick_correction_applications DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION erp_sick_application_complete()')
    op.execute('CREATE CONSTRAINT TRIGGER trg_sick_revision_complete AFTER INSERT ON payroll_sick_leave_calculations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION erp_sick_application_complete()')


def downgrade():
    op.execute('LOCK TABLE payroll_sick_correction_applications, payroll_sick_payroll_revisions IN ACCESS EXCLUSIVE MODE')
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM payroll_sick_correction_applications)')):
        raise RuntimeError('Cannot discard applied sick correction history')
    op.execute('DROP TRIGGER trg_sick_revision_complete ON payroll_sick_leave_calculations')
    op.execute('DROP TRIGGER trg_sick_application_complete ON payroll_sick_correction_applications')
    op.execute('DROP FUNCTION erp_sick_application_complete()')
    op.execute('DROP TRIGGER trg_sick_request_transition ON payroll_sick_corrections')
    op.execute('DROP TRIGGER trg_sick_request_truncate ON payroll_sick_corrections')
    op.execute('DROP FUNCTION erp_sick_request_transition()')
    op.drop_constraint('ck_sick_correction_pending', 'payroll_sick_corrections', type_='check')
    op.create_check_constraint('ck_sick_correction_pending', 'payroll_sick_corrections', "status = 'pending_review'")
    op.drop_table('payroll_sick_payroll_revisions')
    op.drop_table('payroll_sick_correction_applications')
    op.execute('DROP FUNCTION erp_sick_application_immutable()')
