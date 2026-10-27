"""Append-only versions of corrected sick-pay sources.

Revision ID: 13d4a7b8c061
Revises: 13d4a7b8c060
"""
from alembic import op
import sqlalchemy as sa

revision = '13d4a7b8c061'
down_revision = '13d4a7b8c060'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('payroll_sick_leave_calculations', sa.Column('history_creation_xid', sa.BigInteger(), nullable=False, server_default=sa.text('txid_current()')))
    op.add_column('payroll_sick_leave_calculations', sa.Column('revision', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('payroll_sick_leave_calculations', sa.Column('supersedes_id', sa.Integer(), nullable=True))
    op.drop_constraint('uq_payroll_sick_leave_calculations_leave_request', 'payroll_sick_leave_calculations', type_='unique')
    op.create_unique_constraint('uq_payroll_sick_leave_calculations_leave_revision', 'payroll_sick_leave_calculations', ['company_id', 'leave_request_id', 'revision'])
    op.create_unique_constraint('uq_sick_revision_parent', 'payroll_sick_leave_calculations', ['company_id', 'supersedes_id'])
    op.create_foreign_key('fk_sick_revision_parent', 'payroll_sick_leave_calculations', 'payroll_sick_leave_calculations', ['company_id', 'supersedes_id'], ['company_id', 'id'], ondelete='RESTRICT')
    op.create_check_constraint('ck_sick_revision_parent', 'payroll_sick_leave_calculations', '(revision = 1 AND supersedes_id IS NULL) OR (revision > 1 AND supersedes_id IS NOT NULL)')
    op.execute('''CREATE FUNCTION erp_sick_revision_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE parent payroll_sick_leave_calculations%ROWTYPE;
    BEGIN
      IF TG_OP = 'INSERT' THEN
        NEW.history_creation_xid := txid_current();
        IF NEW.supersedes_id IS NOT NULL THEN
          SELECT * INTO parent FROM payroll_sick_leave_calculations
            WHERE company_id=NEW.company_id AND id=NEW.supersedes_id FOR UPDATE;
          IF NOT FOUND OR NEW.revision <> parent.revision+1
            OR NEW.leave_request_id <> parent.leave_request_id
            OR NEW.employment_contract_id <> parent.employment_contract_id
            OR NEW.currency_code <> parent.currency_code THEN
            RAISE EXCEPTION 'Invalid sick correction lineage' USING ERRCODE='23514';
          END IF;
        END IF;
        RETURN NEW;
      END IF;
      IF TG_OP = 'UPDATE' AND (NEW.history_creation_xid IS DISTINCT FROM OLD.history_creation_xid OR NEW.revision IS DISTINCT FROM OLD.revision OR NEW.supersedes_id IS DISTINCT FROM OLD.supersedes_id) THEN
        RAISE EXCEPTION 'Sick revision identity is immutable' USING ERRCODE='23514';
      END IF;
      IF OLD.revision > 1 OR EXISTS (SELECT 1 FROM payroll_sick_leave_calculations s WHERE s.company_id=OLD.company_id AND s.supersedes_id=OLD.id) THEN
        RAISE EXCEPTION 'Corrected sick history is immutable' USING ERRCODE='23514';
      END IF;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$''')
    op.execute('''CREATE TRIGGER trg_sick_revision_guard BEFORE INSERT OR UPDATE OR DELETE
        ON payroll_sick_leave_calculations FOR EACH ROW EXECUTE FUNCTION erp_sick_revision_guard()''')

    op.execute("""CREATE FUNCTION erp_sick_membership_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE owner payroll_sick_leave_calculations%ROWTYPE; protected boolean; cid integer; sid integer;
    BEGIN
      IF TG_OP='TRUNCATE' THEN RAISE EXCEPTION 'Sick history cannot be truncated'; END IF;
      IF TG_OP='UPDATE' AND (NEW.company_id<>OLD.company_id OR NEW.sick_leave_calculation_id<>OLD.sick_leave_calculation_id) THEN
        RAISE EXCEPTION 'Sick history membership is immutable';
      END IF;
      IF TG_OP='DELETE' THEN cid:=OLD.company_id; sid:=OLD.sick_leave_calculation_id;
      ELSE cid:=NEW.company_id; sid:=NEW.sick_leave_calculation_id; END IF;
      SELECT * INTO owner FROM payroll_sick_leave_calculations WHERE company_id=cid AND id=sid FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'Sick calculation not found'; END IF;
      protected := owner.revision>1 OR EXISTS (SELECT 1 FROM payroll_sick_leave_calculations s WHERE s.company_id=cid AND s.supersedes_id=sid)
        OR EXISTS (SELECT 1 FROM payroll_calculation_lines l WHERE l.company_id=cid AND l.source_sick_leave_calculation_id=sid);
      IF protected AND NOT (TG_OP='INSERT' AND owner.history_creation_xid=txid_current()
        AND NOT EXISTS (SELECT 1 FROM payroll_calculation_lines l WHERE l.company_id=cid AND l.source_sick_leave_calculation_id=sid)
        AND NOT EXISTS (SELECT 1 FROM payroll_sick_leave_calculations s WHERE s.company_id=cid AND s.supersedes_id=sid)) THEN
        RAISE EXCEPTION 'Sick history membership is immutable' USING ERRCODE='23514';
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW;
    END $$""")
    op.execute('CREATE TRIGGER trg_sick_membership_guard BEFORE INSERT OR UPDATE OR DELETE ON payroll_sick_leave_calculation_sources FOR EACH ROW EXECUTE FUNCTION erp_sick_membership_guard()')
    for table in ('payroll_sick_leave_calculations','payroll_sick_leave_calculation_sources'):
        op.execute(f'CREATE TRIGGER trg_sick_history_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION erp_sick_membership_guard()')


def downgrade():
    op.execute('LOCK TABLE payroll_sick_leave_calculations IN ACCESS EXCLUSIVE MODE')
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM payroll_sick_leave_calculations WHERE revision > 1)')):
        raise RuntimeError('Cannot discard historical sick revisions')
    op.execute('DROP TRIGGER trg_sick_membership_guard ON payroll_sick_leave_calculation_sources')
    for table in ('payroll_sick_leave_calculations','payroll_sick_leave_calculation_sources'):
        op.execute(f'DROP TRIGGER trg_sick_history_truncate ON {table}')
    op.execute('DROP FUNCTION erp_sick_membership_guard()')
    op.execute('DROP TRIGGER trg_sick_revision_guard ON payroll_sick_leave_calculations')
    op.execute('DROP FUNCTION erp_sick_revision_guard()')
    for name, kind in [('fk_sick_revision_parent', 'foreignkey'), ('ck_sick_revision_parent', 'check'), ('uq_sick_revision_parent', 'unique'), ('uq_payroll_sick_leave_calculations_leave_revision', 'unique')]:
        op.drop_constraint(name, 'payroll_sick_leave_calculations', type_=kind)
    op.create_unique_constraint('uq_payroll_sick_leave_calculations_leave_request', 'payroll_sick_leave_calculations', ['company_id', 'leave_request_id'])
    op.drop_column('payroll_sick_leave_calculations', 'supersedes_id')
    op.drop_column('payroll_sick_leave_calculations', 'revision')

    op.drop_column('payroll_sick_leave_calculations', 'history_creation_xid')
