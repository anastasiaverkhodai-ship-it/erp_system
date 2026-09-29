"""Record partial disposals and existing posted sale sources."""
from alembic import op
import sqlalchemy as sa
revision = '8fa12510c003'
down_revision = '8fa12510c002'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('fixed_asset_disposals', sa.Column('disposal_fraction', sa.Numeric(9,8), nullable=False, server_default='1'))
    for name in ('asset_cost_before', 'asset_salvage_before', 'sale_net_amount'):
        op.add_column('fixed_asset_disposals', sa.Column(name, sa.Numeric(18,2), nullable=True))
    op.add_column('fixed_asset_disposals', sa.Column('sale_source_line_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_fad_sale_source_line','fixed_asset_disposals','journal_entry_lines',['sale_source_line_id'],['id'],ondelete='RESTRICT')
    op.create_index('ix_fad_sale_source_line','fixed_asset_disposals',['sale_source_line_id'])
    op.create_check_constraint('ck_fad_fraction','fixed_asset_disposals','disposal_fraction > 0 AND disposal_fraction <= 1')
    op.create_check_constraint('ck_fad_sale_source','fixed_asset_disposals','(sale_source_line_id IS NULL AND sale_net_amount IS NULL) OR (sale_source_line_id IS NOT NULL AND sale_net_amount > 0)')

def downgrade():
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM fixed_asset_disposals WHERE disposal_fraction <> 1 OR sale_source_line_id IS NOT NULL')):
        raise RuntimeError('Cannot remove partial disposal or sale history; no data was removed')
    op.drop_constraint('ck_fad_sale_source','fixed_asset_disposals',type_='check')
    op.drop_constraint('ck_fad_fraction','fixed_asset_disposals',type_='check')
    op.drop_index('ix_fad_sale_source_line',table_name='fixed_asset_disposals')
    op.drop_constraint('fk_fad_sale_source_line','fixed_asset_disposals',type_='foreignkey')
    for name in ('disposal_fraction','asset_cost_before','asset_salvage_before','sale_source_line_id','sale_net_amount'):
        op.drop_column('fixed_asset_disposals', name)
