"""Add immutable supplier quotation terms for purchase comparison."""
from alembic import op
import sqlalchemy as sa

revision = "d1f8b4c6e935"
down_revision = "c0e7a3b5d824"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('purchase_quotes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('supplier_id', sa.Integer(), nullable=False),
    sa.Column('contract_id', sa.Integer(), nullable=True),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('reference', sa.String(length=100), nullable=False),
    sa.Column('currency_code', sa.String(length=3), nullable=False),
    sa.Column('min_quantity', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('max_quantity', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('unit_price_net', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('unit_price_gross', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('delivery_net', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('delivery_gross', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('lead_time_days', sa.Integer(), nullable=False),
    sa.Column('payment_term_days', sa.Integer(), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=False),
    sa.Column('valid_until', sa.Date(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('withdrawn_by', sa.Integer(), nullable=True),
    sa.Column('withdrawn_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("currency_code = 'UAH'", name='ck_pq_currency'),
    sa.CheckConstraint("delivery_net >= 0 AND delivery_gross >= delivery_net AND delivery_gross < 'Infinity'::numeric", name='ck_pq_delivery'),
    sa.CheckConstraint("max_quantity IS NULL OR (max_quantity >= min_quantity AND max_quantity < 'Infinity'::numeric)", name='ck_pq_max_qty'),
    sa.CheckConstraint("min_quantity > 0 AND min_quantity < 'Infinity'::numeric", name='ck_pq_min_qty'),
    sa.CheckConstraint("unit_price_net >= 0 AND unit_price_gross >= unit_price_net AND unit_price_gross < 'Infinity'::numeric", name='ck_pq_prices'),
    sa.CheckConstraint('lead_time_days >= 0 AND payment_term_days >= 0', name='ck_pq_terms'),
    sa.CheckConstraint('valid_until >= valid_from', name='ck_pq_dates'),
    sa.ForeignKeyConstraint(['company_id', 'product_id'], ['products.company_id', 'products.id'], name='fk_pq_product', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['company_id', 'supplier_id', 'contract_id'], ['contracts.company_id', 'contracts.counterparty_id', 'contracts.id'], name='fk_pq_contract', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['company_id', 'supplier_id'], ['counterparties.company_id', 'counterparties.id'], name='fk_pq_supplier', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['withdrawn_by'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('company_id', 'supplier_id', 'reference', 'product_id', name='uq_pq_reference_product')
    )
    op.create_index('ix_pq_company_product_active', 'purchase_quotes', ['company_id', 'product_id', 'is_active'], unique=False)

def downgrade():
    op.drop_index('ix_pq_company_product_active', table_name='purchase_quotes')
    op.drop_table('purchase_quotes')
