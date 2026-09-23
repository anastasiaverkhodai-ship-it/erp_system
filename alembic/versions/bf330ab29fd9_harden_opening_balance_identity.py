"""Harden opening balance request identity and tenant-scoped journal provenance."""
from alembic import op
import sqlalchemy as sa
revision='bf330ab29fd9'
down_revision='af229fa18ec8'
branch_labels=None
depends_on=None

def upgrade():
    op.create_unique_constraint('uq_je_company_id','journal_entries',['company_id','id'])
    op.add_column('opening_balances',sa.Column('request_key',sa.String(255),nullable=True))
    op.add_column('opening_balances',sa.Column('request_fingerprint',sa.String(64),nullable=True))
    op.execute("UPDATE opening_balances SET request_key='legacy:' || id::text, request_fingerprint=repeat('0',64)")
    op.alter_column('opening_balances','request_key',nullable=False)
    op.alter_column('opening_balances','request_fingerprint',nullable=False)
    op.create_unique_constraint('uq_opening_company_request','opening_balances',['company_id','request_key'])
    op.create_foreign_key('fk_opening_company_journal','opening_balances','journal_entries',['company_id','journal_entry_id'],['company_id','id'],ondelete='RESTRICT')

def downgrade():
    op.drop_constraint('fk_opening_company_journal','opening_balances',type_='foreignkey')
    op.drop_constraint('uq_opening_company_request','opening_balances',type_='unique')
    op.drop_column('opening_balances','request_fingerprint')
    op.drop_column('opening_balances','request_key')
    op.drop_constraint('uq_je_company_id','journal_entries',type_='unique')
