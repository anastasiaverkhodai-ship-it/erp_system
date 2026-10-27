"""Confirmed sick-correction returns; no automatic employee recovery.
Revision ID: 13d4a7b8c063
Revises: 13d4a7b8c062
"""
from alembic import op
import sqlalchemy as sa
revision = '13d4a7b8c063'
down_revision = '13d4a7b8c062'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('payroll_sick_returns',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('company_id',sa.Integer(),nullable=False),
        sa.Column('payroll_calculation_id',sa.Integer(),nullable=False),
        sa.Column('journal_entry_id',sa.Integer(),nullable=False),
        sa.Column('kind',sa.String(20),nullable=False),
        sa.Column('amount',sa.Numeric(18,2),nullable=False),
        sa.Column('request_key',sa.String(100),nullable=False),
        sa.Column('request_fingerprint',sa.String(64),nullable=False),
        sa.Column('document_reference',sa.String(500),nullable=False),
        sa.Column('created_by',sa.Integer(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.UniqueConstraint('company_id','journal_entry_id',name='uq_sick_return_journal'),
        sa.UniqueConstraint('company_id','request_key',name='uq_sick_return_request'),
        sa.ForeignKeyConstraint(['company_id','payroll_calculation_id'],['payroll_calculations.company_id','payroll_calculations.id'],name='fk_sick_return_calculation',ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['company_id','journal_entry_id'],['journal_entries.company_id','journal_entries.id'],name='fk_sick_return_journal',ondelete='RESTRICT'),
        sa.CheckConstraint("kind IN ('employee','insurer')",name='ck_sick_return_kind'),
        sa.CheckConstraint("amount > 0 AND amount <> 'NaN'::numeric",name='ck_sick_return_amount'),
        sa.CheckConstraint('length(trim(document_reference)) >= 10',name='ck_sick_return_reference'))
    op.execute('CREATE TRIGGER trg_sick_return_immutable BEFORE UPDATE OR DELETE ON payroll_sick_returns FOR EACH ROW EXECUTE FUNCTION erp_sick_application_immutable()')
    op.execute('CREATE TRIGGER trg_sick_return_truncate BEFORE TRUNCATE ON payroll_sick_returns FOR EACH STATEMENT EXECUTE FUNCTION erp_sick_application_immutable()')


def downgrade():
    op.execute('LOCK TABLE payroll_sick_returns IN ACCESS EXCLUSIVE MODE')
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM payroll_sick_returns)')):
        raise RuntimeError('Cannot discard confirmed sick correction returns')
    op.drop_table('payroll_sick_returns')
