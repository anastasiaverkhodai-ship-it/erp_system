"""Use the existing bank reconciliation ledger for payroll payouts.
Revision ID: 13d4a7b8c004
Revises: 13d4a7b8c003
"""
from alembic import op
import sqlalchemy as sa
revision='13d4a7b8c004'
down_revision='13d4a7b8c003'
branch_labels=None
depends_on=None
TABLES=(('bank_statement_reconciliations','bank_reconciliation'),
        ('bank_statement_reconciliation_active_links','bank_reconciliation_link'))


def upgrade():
    for table, short in TABLES:
        op.add_column(table,sa.Column('payroll_disbursement_id',sa.Integer(),nullable=True))
        op.alter_column(table,'payment_id',nullable=True)
        op.create_foreign_key(f'fk_{short}_payroll',table,'payroll_disbursements',
            ['company_id','payroll_disbursement_id'],['company_id','id'],ondelete='RESTRICT')
        op.create_check_constraint(f'ck_{short}_target',table,'num_nonnulls(payment_id, payroll_disbursement_id) = 1')


def downgrade():
    condition='SELECT EXISTS (SELECT 1 FROM bank_statement_reconciliations WHERE payroll_disbursement_id IS NOT NULL)'
    if op.get_context().as_sql:
        op.execute("DO $$ BEGIN IF ("+condition+") THEN RAISE EXCEPTION 'Payroll bank history must be preserved'; END IF; END $$")
    elif op.get_bind().scalar(sa.text(condition)):
        raise RuntimeError('Payroll bank history must be preserved')
    for table, short in reversed(TABLES):
        op.drop_constraint(f'ck_{short}_target',table,type_='check')
        op.drop_constraint(f'fk_{short}_payroll',table,type_='foreignkey')
        op.alter_column(table,'payment_id',nullable=False)
        op.drop_column(table,'payroll_disbursement_id')
