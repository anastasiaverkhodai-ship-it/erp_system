"""Add expense-source linkage and immutable landed-cost valuation."""
from alembic import op
import sqlalchemy as sa

revision = "c0e7a3b5d824"
down_revision = "b9d6f2a4c713"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('purchase_landed_cost_valuation_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('allocation_event_id', sa.Integer(), nullable=False),
    sa.Column('destination_key', sa.String(length=100), nullable=False),
    sa.Column('destination_kind', sa.String(length=10), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('warehouse_id', sa.Integer(), nullable=False),
    sa.Column('stock_lot_id', sa.Integer(), nullable=True),
    sa.Column('inventory_cost_entry_id', sa.Integer(), nullable=True),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('recognition_date', sa.Date(), nullable=False),
    sa.Column('debit_account_id', sa.Integer(), nullable=False),
    sa.Column('credit_account_id', sa.Integer(), nullable=False),
    sa.Column('journal_entry_id', sa.Integer(), nullable=True),
    sa.Column('reversal_of_id', sa.Integer(), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("amount > 0 AND amount < 'Infinity'::numeric", name='ck_plcv_amount'),
    sa.CheckConstraint("destination_kind IN ('on_hand', 'issued')", name='ck_plcv_kind'),
    sa.CheckConstraint('reversal_of_id IS NULL OR reversal_of_id <> id', name='ck_plcv_not_self'),
    sa.ForeignKeyConstraint(['allocation_event_id'], ['purchase_landed_cost_allocation_events.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['company_id', 'reversal_of_id'], ['purchase_landed_cost_valuation_events.company_id', 'purchase_landed_cost_valuation_events.id'], name='fk_plcv_reversal', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['credit_account_id'], ['accounts.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['debit_account_id'], ['accounts.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['inventory_cost_entry_id'], ['inventory_cost_entries.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['journal_entry_id'], ['journal_entries.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['stock_lot_id'], ['stock_lots.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['warehouse_id'], ['warehouses.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('company_id', 'id', name='uq_plcv_company_id'),
    sa.UniqueConstraint('journal_entry_id', name='uq_plcv_journal'),
    sa.UniqueConstraint('reversal_of_id', name='uq_plcv_reversal')
    )
    op.create_index('ix_plcv_allocation', 'purchase_landed_cost_valuation_events', ['company_id', 'allocation_event_id', 'id'], unique=False)
    op.create_index('ix_plcv_issue', 'purchase_landed_cost_valuation_events', ['company_id', 'inventory_cost_entry_id', 'recognition_date'], unique=False)
    op.create_table('purchase_landed_cost_capitalizations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('landed_cost_event_id', sa.Integer(), nullable=False),
    sa.Column('source_journal_entry_line_id', sa.Integer(), nullable=False),
    sa.Column('request_key', sa.String(length=100), nullable=False),
    sa.ForeignKeyConstraint(['company_id', 'landed_cost_event_id'], ['purchase_landed_cost_events.company_id', 'purchase_landed_cost_events.id'], name='fk_plcc_event', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['source_journal_entry_line_id'], ['journal_entry_lines.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('company_id', 'request_key', name='uq_plcc_request'),
    sa.UniqueConstraint('landed_cost_event_id', name='uq_plcc_event')
    )

def downgrade():
    op.drop_table('purchase_landed_cost_capitalizations')
    op.drop_index('ix_plcv_issue', table_name='purchase_landed_cost_valuation_events')
    op.drop_index('ix_plcv_allocation', table_name='purchase_landed_cost_valuation_events')
    op.drop_table('purchase_landed_cost_valuation_events')
