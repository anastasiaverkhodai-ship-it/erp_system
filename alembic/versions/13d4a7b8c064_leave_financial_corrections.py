"""Extend the shared financial correction ledger with immutable leave evidence.

The historical sick table names stay stable for deployed settlement consumers.
Revision ID: 13d4a7b8c064
Revises: 13d4a7b8c063
"""
from alembic import op
import sqlalchemy as sa
revision='13d4a7b8c064'
down_revision='13d4a7b8c063'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('payroll_leave_compensation_extra_sources',
        sa.Column('id',sa.Integer(),primary_key=True),sa.Column('company_id',sa.Integer(),nullable=False),
        sa.Column('compensation_id',sa.Integer(),nullable=False),sa.Column('account_id',sa.Integer(),nullable=False),
        sa.Column('acquisition_bucket',sa.String(16),nullable=False),
        sa.UniqueConstraint('company_id','id',name='uq_leave_extra_company'),
        sa.UniqueConstraint('company_id','compensation_id','account_id','acquisition_bucket',name='uq_leave_extra_bucket'),
        sa.ForeignKeyConstraint(['company_id','compensation_id'],['payroll_leave_compensations.company_id','payroll_leave_compensations.id'],name='fk_leave_extra_compensation',ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['company_id','account_id'],['payroll_leave_accounts.company_id','payroll_leave_accounts.id'],name='fk_leave_extra_account',ondelete='RESTRICT'),
        sa.CheckConstraint("acquisition_bucket IN ('before_2024','from_2024')",name='ck_leave_extra_bucket'))
    op.add_column('payroll_calculation_lines',sa.Column('source_leave_compensation_extra_id',sa.Integer(),nullable=True))
    op.create_foreign_key('fk_pcl_leave_compensation_extra','payroll_calculation_lines','payroll_leave_compensation_extra_sources',
        ['company_id','source_leave_compensation_extra_id'],['company_id','id'],ondelete='RESTRICT')
    op.drop_constraint('ck_pcl_leave_compensation_source','payroll_calculation_lines',type_='check')
    op.create_check_constraint('ck_pcl_leave_compensation_source','payroll_calculation_lines',
        "(CASE WHEN source_leave_compensation_line_id IS NOT NULL THEN 1 ELSE 0 END + CASE WHEN source_leave_compensation_extra_id IS NOT NULL THEN 1 ELSE 0 END) = CASE WHEN line_type='leave_compensation' THEN 1 ELSE 0 END")
    op.execute("""CREATE FUNCTION erp_leave_extra_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      PERFORM id FROM companies WHERE id=NEW.company_id FOR UPDATE;
      IF NOT EXISTS (SELECT 1 FROM payroll_leave_compensations c JOIN payroll_leave_accounts a
          ON a.company_id=c.company_id AND a.employment_contract_id=c.employment_contract_id
          WHERE c.company_id=NEW.company_id AND c.id=NEW.compensation_id AND a.id=NEW.account_id)
        OR EXISTS (SELECT 1 FROM payroll_leave_compensation_lines l WHERE l.company_id=NEW.company_id
          AND l.compensation_id=NEW.compensation_id AND l.account_id=NEW.account_id AND l.acquisition_bucket=NEW.acquisition_bucket) THEN
        RAISE EXCEPTION 'Extra compensation source requires a new matching right bucket' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute('CREATE TRIGGER trg_leave_extra_insert BEFORE INSERT ON payroll_leave_compensation_extra_sources FOR EACH ROW EXECUTE FUNCTION erp_leave_extra_guard()')
    op.execute('CREATE TRIGGER trg_leave_extra_immutable BEFORE UPDATE OR DELETE ON payroll_leave_compensation_extra_sources FOR EACH ROW EXECUTE FUNCTION erp_sick_application_immutable()')
    op.execute('CREATE TRIGGER trg_leave_extra_truncate BEFORE TRUNCATE ON payroll_leave_compensation_extra_sources FOR EACH STATEMENT EXECUTE FUNCTION erp_sick_application_immutable()')
    op.add_column('payroll_sick_correction_applications',sa.Column('source_kind',sa.String(10),nullable=False,server_default='sick'))
    for column in ('original_sick_id','replacement_sick_id'):
        op.alter_column('payroll_sick_correction_applications',column,nullable=True,existing_type=sa.Integer())
    op.create_check_constraint('ck_payroll_correction_source_kind','payroll_sick_correction_applications',
        "(source_kind='sick' AND original_sick_id IS NOT NULL AND replacement_sick_id IS NOT NULL) OR (source_kind='leave' AND original_sick_id IS NULL AND replacement_sick_id IS NULL)")
    op.create_table('payroll_leave_correction_requests',
        sa.Column('id',sa.Integer(),primary_key=True),sa.Column('company_id',sa.Integer(),nullable=False),
        sa.Column('request_key',sa.String(100),nullable=False),sa.Column('request_fingerprint',sa.String(64),nullable=False),
        sa.Column('created_by',sa.Integer(),nullable=False),sa.Column('payload',sa.JSON(),nullable=False),
        sa.UniqueConstraint('company_id','id',name='uq_leave_request_company'),
        sa.UniqueConstraint('company_id','request_key',name='uq_leave_request_key'))
    op.create_table('payroll_leave_corrections',
        sa.Column('id',sa.Integer(),primary_key=True),sa.Column('company_id',sa.Integer(),nullable=False),
        sa.Column('request_id',sa.Integer(),nullable=False),
        sa.Column('application_id',sa.Integer(),nullable=False),sa.Column('target_kind',sa.String(20),nullable=False),
        sa.Column('target_id',sa.Integer(),nullable=False),sa.Column('revision',sa.Integer(),nullable=False),
        sa.Column('supersedes_id',sa.Integer()),sa.Column('creation_xid',sa.BigInteger(),nullable=False,server_default=sa.text('txid_current()')),sa.Column('reviewed_by',sa.Integer(),nullable=False),
        sa.Column('original_snapshot',sa.JSON(),nullable=False),sa.Column('replacement_snapshot',sa.JSON(),nullable=False),
        sa.UniqueConstraint('company_id','id',name='uq_leave_correction_company'),
        sa.UniqueConstraint('company_id','application_id',name='uq_leave_correction_app'),
        sa.UniqueConstraint('company_id','request_id',name='uq_leave_correction_request'),
        sa.ForeignKeyConstraint(['company_id','request_id'],['payroll_leave_correction_requests.company_id','payroll_leave_correction_requests.id'],name='fk_leave_correction_request',ondelete='RESTRICT'),
        sa.UniqueConstraint('company_id','target_kind','target_id','revision',name='uq_leave_correction_revision'),
        sa.UniqueConstraint('company_id','supersedes_id',name='uq_leave_correction_parent'),
        sa.ForeignKeyConstraint(['company_id','application_id'],['payroll_sick_correction_applications.company_id','payroll_sick_correction_applications.id'],name='fk_leave_correction_app',ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['company_id','supersedes_id'],['payroll_leave_corrections.company_id','payroll_leave_corrections.id'],name='fk_leave_correction_parent',ondelete='RESTRICT'),
        sa.CheckConstraint("target_kind IN ('vacation','compensation')",name='ck_leave_correction_kind'),
        sa.CheckConstraint("revision > 0 AND ((revision=1 AND supersedes_id IS NULL) OR (revision>1 AND supersedes_id IS NOT NULL))",name='ck_leave_correction_revision'),
        sa.CheckConstraint('target_id > 0',name='ck_leave_correction_target'))
    op.create_table('payroll_leave_day_corrections',
        sa.Column('id',sa.Integer(),primary_key=True),sa.Column('company_id',sa.Integer(),nullable=False),
        sa.Column('correction_id',sa.Integer(),nullable=False),sa.Column('allocation_id',sa.Integer(),nullable=False),
        sa.Column('days',sa.Integer(),nullable=False),
        sa.UniqueConstraint('company_id','correction_id','allocation_id',name='uq_leave_day_correction_allocation'),
        sa.ForeignKeyConstraint(['company_id','correction_id'],['payroll_leave_corrections.company_id','payroll_leave_corrections.id'],name='fk_leave_day_correction',ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['company_id','allocation_id'],['payroll_leave_balance_entries.company_id','payroll_leave_balance_entries.id'],name='fk_leave_day_allocation',ondelete='RESTRICT'),
        sa.CheckConstraint('days > 0',name='ck_leave_day_positive'))
    op.create_table('payroll_leave_transfers',
        sa.Column('id',sa.Integer(),primary_key=True),sa.Column('company_id',sa.Integer(),nullable=False),
        sa.Column('correction_id',sa.Integer(),nullable=False),sa.Column('leave_request_id',sa.Integer(),nullable=False),
        sa.UniqueConstraint('company_id','leave_request_id',name='uq_leave_transfer_request'),
        sa.ForeignKeyConstraint(['company_id','correction_id'],['payroll_leave_corrections.company_id','payroll_leave_corrections.id'],name='fk_leave_transfer_correction',ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['company_id','leave_request_id'],['leave_requests.company_id','leave_requests.id'],name='fk_leave_transfer_request',ondelete='RESTRICT'))
    op.execute("""CREATE FUNCTION erp_leave_transfer_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE c payroll_leave_corrections%ROWTYPE; l leave_requests%ROWTYPE; r payroll_leave_correction_requests%ROWTYPE;
    BEGIN
      SELECT * INTO c FROM payroll_leave_corrections WHERE company_id=NEW.company_id AND id=NEW.correction_id FOR UPDATE;
      SELECT * INTO l FROM leave_requests WHERE company_id=NEW.company_id AND id=NEW.leave_request_id;
      SELECT * INTO r FROM payroll_leave_correction_requests WHERE company_id=c.company_id AND id=c.request_id;
      IF c.target_kind IS DISTINCT FROM 'vacation' OR c.creation_xid IS DISTINCT FROM txid_current()
        OR l.status IS DISTINCT FROM 'approved'
        OR l.employment_contract_id IS DISTINCT FROM (c.replacement_snapshot->>'contract_id')::integer
        OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(r.payload::jsonb->'transferred_ranges') x
          WHERE (x->>'start')::date=l.start_date AND (x->>'end')::date=l.end_date)
        OR EXISTS (SELECT 1 FROM payroll_leave_transfers t JOIN leave_requests p
          ON p.company_id=t.company_id AND p.id=t.leave_request_id
          WHERE t.company_id=NEW.company_id AND t.correction_id=NEW.correction_id
            AND p.start_date=l.start_date AND p.end_date=l.end_date) THEN
        RAISE EXCEPTION 'Leave transfer must match a new reviewed correction' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute('CREATE TRIGGER trg_leave_transfer_guard BEFORE INSERT ON payroll_leave_transfers FOR EACH ROW EXECUTE FUNCTION erp_leave_transfer_guard()')
    op.execute('CREATE TRIGGER trg_leave_transfer_immutable BEFORE UPDATE OR DELETE ON payroll_leave_transfers FOR EACH ROW EXECUTE FUNCTION erp_sick_application_immutable()')
    op.execute('CREATE TRIGGER trg_leave_transfer_truncate BEFORE TRUNCATE ON payroll_leave_transfers FOR EACH STATEMENT EXECUTE FUNCTION erp_sick_application_immutable()')
    op.execute('''CREATE FUNCTION erp_leave_day_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE e payroll_leave_balance_entries%ROWTYPE; c payroll_leave_corrections%ROWTYPE; total numeric;
    BEGIN
      SELECT * INTO e FROM payroll_leave_balance_entries WHERE company_id=NEW.company_id AND id=NEW.allocation_id FOR UPDATE;
      SELECT * INTO c FROM payroll_leave_corrections WHERE company_id=NEW.company_id AND id=NEW.correction_id;
      IF e.kind IS DISTINCT FROM 'use' OR c.target_kind IS DISTINCT FROM 'vacation'
        OR c.creation_xid IS DISTINCT FROM txid_current()
        OR NOT EXISTS (SELECT 1 FROM payroll_vacation_calculations v WHERE v.company_id=c.company_id
          AND v.id=c.target_id AND v.leave_request_id=e.leave_request_id) THEN
        RAISE EXCEPTION 'Leave days require a matching new correction' USING ERRCODE='23514';
      END IF;
      SELECT coalesce(sum(days),0) INTO total FROM payroll_leave_day_corrections WHERE company_id=NEW.company_id AND allocation_id=NEW.allocation_id;
      IF total+NEW.days > -e.days THEN RAISE EXCEPTION 'Leave day return exceeds allocation' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$''')
    op.execute('CREATE TRIGGER trg_leave_day_guard BEFORE INSERT ON payroll_leave_day_corrections FOR EACH ROW EXECUTE FUNCTION erp_leave_day_guard()')
    op.execute('CREATE TRIGGER trg_leave_days_immutable BEFORE UPDATE OR DELETE ON payroll_leave_day_corrections FOR EACH ROW EXECUTE FUNCTION erp_sick_application_immutable()')
    op.execute('CREATE TRIGGER trg_leave_days_truncate BEFORE TRUNCATE ON payroll_leave_day_corrections FOR EACH STATEMENT EXECUTE FUNCTION erp_sick_application_immutable()')
    op.execute('''CREATE FUNCTION erp_leave_correction_complete() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE evidence payroll_leave_corrections%ROWTYPE; app payroll_sick_correction_applications%ROWTYPE;
    BEGIN
      IF TG_TABLE_NAME='payroll_sick_correction_applications' THEN
        IF NEW.source_kind <> 'leave' THEN RETURN NEW; END IF;
        SELECT * INTO evidence FROM payroll_leave_corrections WHERE company_id=NEW.company_id AND application_id=NEW.id;
        IF NOT FOUND THEN RAISE EXCEPTION 'Leave application requires source evidence' USING ERRCODE='23514'; END IF;
      ELSE
        SELECT * INTO evidence FROM payroll_leave_corrections WHERE id=NEW.id;
      END IF;
      SELECT * INTO app FROM payroll_sick_correction_applications WHERE company_id=evidence.company_id AND id=evidence.application_id;
      IF app.source_kind <> 'leave' OR app.created_by=evidence.reviewed_by
          OR NOT EXISTS (SELECT 1 FROM users WHERE id=evidence.reviewed_by) THEN
        RAISE EXCEPTION 'Leave correction requires independent review' USING ERRCODE='23514';
      END IF;
      IF NOT EXISTS (SELECT 1 FROM payroll_leave_correction_requests r
        WHERE r.company_id=evidence.company_id AND r.id=evidence.request_id AND r.created_by=app.created_by
          AND r.request_fingerprint=app.request_fingerprint
          AND r.payload->>'target_kind'=evidence.target_kind
          AND (r.payload->>'target_id')::integer=evidence.target_id
          AND (r.payload->>'correction_date')::date=app.correction_date
          AND r.payload::jsonb->'expected_source'=evidence.original_snapshot::jsonb
          AND r.payload::jsonb->'expected_replacement'=evidence.replacement_snapshot::jsonb) THEN
        RAISE EXCEPTION 'Leave correction must retain its request initiator' USING ERRCODE='23514';
      END IF;
      IF evidence.target_kind='vacation' THEN
        IF (SELECT count(*) FROM payroll_leave_transfers t WHERE t.company_id=evidence.company_id AND t.correction_id=evidence.id)
          <> (SELECT jsonb_array_length(r.payload::jsonb->'transferred_ranges') FROM payroll_leave_correction_requests r
              WHERE r.company_id=evidence.company_id AND r.id=evidence.request_id) THEN
          RAISE EXCEPTION 'Leave correction must include all approved transfers' USING ERRCODE='23514';
        END IF;
        IF (SELECT coalesce(sum(days),0) FROM payroll_leave_day_corrections d WHERE d.company_id=evidence.company_id AND d.correction_id=evidence.id)
          <> jsonb_array_length(evidence.original_snapshot::jsonb->'used_dates')-jsonb_array_length(evidence.replacement_snapshot::jsonb->'used_dates') THEN
          RAISE EXCEPTION 'Leave correction must reconcile returned days' USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM payroll_vacation_calculations WHERE company_id=evidence.company_id AND id=evidence.target_id) THEN
          RAISE EXCEPTION 'Invalid vacation correction source' USING ERRCODE='23514'; END IF;
      ELSE
        IF NOT EXISTS (SELECT 1 FROM payroll_leave_compensations WHERE company_id=evidence.company_id AND id=evidence.target_id) THEN
          RAISE EXCEPTION 'Invalid compensation correction source' USING ERRCODE='23514'; END IF;
      END IF;
      IF evidence.supersedes_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM payroll_leave_corrections p WHERE p.company_id=evidence.company_id
          AND p.id=evidence.supersedes_id AND p.target_kind=evidence.target_kind
          AND p.target_id=evidence.target_id AND p.revision=evidence.revision-1
          AND p.replacement_snapshot::jsonb=evidence.original_snapshot::jsonb) THEN
        RAISE EXCEPTION 'Invalid leave correction lineage' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$''')
    for table in ('payroll_leave_corrections','payroll_sick_correction_applications'):
        op.execute(f'CREATE CONSTRAINT TRIGGER trg_leave_complete_{table} AFTER INSERT ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION erp_leave_correction_complete()')
    op.execute("""CREATE FUNCTION erp_leave_extra_earning_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE selected_evidence integer;
    BEGIN
      IF NEW.source_leave_compensation_extra_id IS NULL THEN RETURN NEW; END IF;
      WITH RECURSIVE lineage(id,depth,path) AS (
        SELECT NEW.payroll_calculation_id,0,ARRAY[NEW.payroll_calculation_id]
        UNION ALL
        SELECT r.original_payroll_calculation_id,l.depth+1,l.path||r.original_payroll_calculation_id
          FROM lineage l JOIN payroll_corrections r ON r.company_id=NEW.company_id AND r.replacement_payroll_calculation_id=l.id
          WHERE NOT r.original_payroll_calculation_id=ANY(l.path)
      ), candidates AS (
        SELECT e.id,e.revision,l.depth,0 AS priority FROM lineage l
          JOIN payroll_sick_correction_applications a ON a.company_id=NEW.company_id AND a.payroll_calculation_ids::jsonb @> to_jsonb(ARRAY[l.id])
          JOIN payroll_leave_corrections e ON e.company_id=a.company_id AND e.application_id=a.id AND e.target_kind='compensation'
          JOIN payroll_leave_compensation_extra_sources x ON x.company_id=e.company_id AND x.compensation_id=e.target_id
          WHERE x.id=NEW.source_leave_compensation_extra_id
        UNION ALL
        SELECT e.id,e.revision,l.depth,1 FROM lineage l
          JOIN payroll_calculations pc ON pc.company_id=NEW.company_id AND pc.id=l.id
          JOIN payroll_leave_compensation_extra_sources x ON x.company_id=pc.company_id AND x.id=NEW.source_leave_compensation_extra_id
          JOIN payroll_leave_corrections e ON e.company_id=x.company_id AND e.target_kind='compensation' AND e.target_id=x.compensation_id
            AND (pc.minimum_wage_assessment::jsonb #>> ARRAY['correction_basis','payload','leave_correction_sources','compensation:'||x.compensation_id::text])::integer=e.id
      ) SELECT id INTO selected_evidence FROM candidates ORDER BY priority,depth,revision DESC LIMIT 1;
      IF NOT EXISTS (
        SELECT 1 FROM payroll_leave_compensation_extra_sources x
        JOIN payroll_leave_compensations h ON h.company_id=x.company_id AND h.id=x.compensation_id
        JOIN payroll_calculations pc ON pc.company_id=h.company_id AND pc.payroll_input_id=h.payroll_input_id
        JOIN payroll_leave_corrections e ON e.company_id=h.company_id AND e.target_kind='compensation' AND e.target_id=h.id
        JOIN payroll_sick_correction_applications a ON a.company_id=e.company_id AND a.id=e.application_id
        CROSS JOIN LATERAL jsonb_array_elements(e.replacement_snapshot::jsonb->'lines') item
        WHERE x.company_id=NEW.company_id AND x.id=NEW.source_leave_compensation_extra_id
          AND pc.id=NEW.payroll_calculation_id AND pc.employment_contract_id=h.employment_contract_id
          AND e.id=selected_evidence
          AND item->>'source_kind'='extra' AND (item->>'source_id')::integer=x.id
          AND (item->>'account_id')::integer=x.account_id AND item->>'bucket'=x.acquisition_bucket
          AND (item->>'quantity')::numeric=NEW.quantity AND (item->>'amount')::numeric=NEW.amount
          AND (item->>'rate')::numeric=NEW.rate
      ) THEN RAISE EXCEPTION 'Additional compensation earning requires matching reviewed evidence' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$""")
    op.execute('CREATE CONSTRAINT TRIGGER trg_leave_extra_earning AFTER INSERT ON payroll_calculation_lines DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION erp_leave_extra_earning_guard()')
    op.execute('CREATE TRIGGER trg_leave_request_immutable BEFORE UPDATE OR DELETE ON payroll_leave_correction_requests FOR EACH ROW EXECUTE FUNCTION erp_sick_application_immutable()')
    op.execute('CREATE TRIGGER trg_leave_request_truncate BEFORE TRUNCATE ON payroll_leave_correction_requests FOR EACH STATEMENT EXECUTE FUNCTION erp_sick_application_immutable()')
    op.execute('CREATE TRIGGER trg_leave_correction_immutable BEFORE UPDATE OR DELETE ON payroll_leave_corrections FOR EACH ROW EXECUTE FUNCTION erp_sick_application_immutable()')
    op.execute('CREATE TRIGGER trg_leave_correction_truncate BEFORE TRUNCATE ON payroll_leave_corrections FOR EACH STATEMENT EXECUTE FUNCTION erp_sick_application_immutable()')


def downgrade():
    op.execute('LOCK TABLE payroll_sick_correction_applications,payroll_leave_corrections,payroll_leave_correction_requests IN ACCESS EXCLUSIVE MODE')
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM payroll_sick_correction_applications WHERE source_kind='leave') OR EXISTS(SELECT 1 FROM payroll_leave_correction_requests)")):
        raise RuntimeError('Cannot discard leave correction history or pending requests')
    op.execute('DROP TRIGGER trg_leave_extra_earning ON payroll_calculation_lines')
    op.execute('DROP FUNCTION erp_leave_extra_earning_guard()')
    op.execute('DROP TRIGGER trg_leave_complete_payroll_sick_correction_applications ON payroll_sick_correction_applications')
    op.drop_table('payroll_leave_transfers')
    op.execute('DROP FUNCTION erp_leave_transfer_guard()')
    op.drop_table('payroll_leave_day_corrections')
    op.execute('DROP FUNCTION erp_leave_day_guard()')
    op.drop_table('payroll_leave_corrections')
    op.drop_table('payroll_leave_correction_requests')
    op.execute('DROP FUNCTION erp_leave_correction_complete()')
    op.drop_constraint('ck_payroll_correction_source_kind','payroll_sick_correction_applications',type_='check')
    for column in ('original_sick_id','replacement_sick_id'):
        op.alter_column('payroll_sick_correction_applications',column,nullable=False,existing_type=sa.Integer())
    op.drop_column('payroll_sick_correction_applications','source_kind')
    op.drop_constraint('fk_pcl_leave_compensation_extra','payroll_calculation_lines',type_='foreignkey')
    op.drop_constraint('ck_pcl_leave_compensation_source','payroll_calculation_lines',type_='check')
    op.drop_column('payroll_calculation_lines','source_leave_compensation_extra_id')
    op.create_check_constraint('ck_pcl_leave_compensation_source','payroll_calculation_lines',
        "(line_type = 'leave_compensation') = (source_leave_compensation_line_id IS NOT NULL)")
    op.drop_table('payroll_leave_compensation_extra_sources')
    op.execute('DROP FUNCTION erp_leave_extra_guard()')
