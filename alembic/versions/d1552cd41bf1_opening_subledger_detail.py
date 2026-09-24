"""Opening stock/debt provenance without duplicate GL.

Revision ID: d1552cd41bf1
Revises: c0441bc30ae0
"""
from alembic import op
import sqlalchemy as sa
revision='d1552cd41bf1'
down_revision='c0441bc30ae0'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('opening_balance_details',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('company_id',sa.Integer(),nullable=False),
        sa.Column('opening_balance_id',sa.Integer(),nullable=False),
        sa.Column('request_fingerprint',sa.String(64),nullable=False),
        sa.Column('stock_document_id',sa.Integer(),nullable=True),
        sa.Column('stock_document_type',sa.String(10),nullable=False),
        sa.Column('created_by',sa.Integer(),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.CheckConstraint("stock_document_type = 'receipt'",name='ck_opening_detail_receipt'),
        sa.UniqueConstraint('stock_document_id'),
        sa.UniqueConstraint('company_id','opening_balance_id',name='uq_opening_detail_source'),
        sa.ForeignKeyConstraint(['company_id','opening_balance_id'],['opening_balances.company_id','opening_balances.id'],name='fk_opening_detail_source',ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['company_id','stock_document_id','stock_document_type'],['documents.company_id','documents.id','documents.document_type'],name='fk_opening_detail_stock',ondelete='RESTRICT'))
    op.add_column('counterparty_open_items',sa.Column('opening_balance_id',sa.Integer(),nullable=True))
    op.add_column('counterparty_open_items',sa.Column('opening_reference',sa.String(255),nullable=True))
    op.alter_column('counterparty_open_items','trade_document_id',existing_type=sa.Integer(),nullable=True)
    op.create_index('ix_counterparty_open_items_opening_balance_id','counterparty_open_items',['opening_balance_id'])
    op.create_foreign_key('fk_open_item_opening','counterparty_open_items','opening_balances',
        ['company_id','opening_balance_id'],['company_id','id'],ondelete='RESTRICT')
    op.create_check_constraint('ck_open_item_single_source','counterparty_open_items',
        '(trade_document_id IS NOT NULL AND opening_balance_id IS NULL) OR (trade_document_id IS NULL AND opening_balance_id IS NOT NULL)')


def downgrade():
    # Never discard imported stock/debt provenance, even after business reversal.
    connection=op.get_bind()
    if connection.execute(sa.text('SELECT EXISTS (SELECT 1 FROM opening_balance_details) OR EXISTS '
            '(SELECT 1 FROM counterparty_open_items WHERE opening_balance_id IS NOT NULL)')).scalar():
        raise RuntimeError('Opening detail exists; downgrade would destroy migration provenance')
    op.drop_constraint('ck_open_item_single_source','counterparty_open_items',type_='check')
    op.drop_constraint('fk_open_item_opening','counterparty_open_items',type_='foreignkey')
    op.drop_index('ix_counterparty_open_items_opening_balance_id',table_name='counterparty_open_items')
    op.alter_column('counterparty_open_items','trade_document_id',existing_type=sa.Integer(),nullable=False)
    op.drop_column('counterparty_open_items','opening_reference')
    op.drop_column('counterparty_open_items','opening_balance_id')
    op.drop_table('opening_balance_details')
