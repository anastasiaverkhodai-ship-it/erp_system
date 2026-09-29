"""Allow depreciation reversals without allowing duplicate reversal events."""
from alembic import op
import sqlalchemy as sa
revision = '8fa12510c001'
down_revision = '739f64c150c1'
branch_labels = None
depends_on = None

def upgrade():
    op.drop_constraint('uq_fa_depr_asset_period','fixed_asset_depreciations',type_='unique')
    op.create_index('ix_fa_depr_asset_period','fixed_asset_depreciations',['company_id','fixed_asset_id','period_start','period_end'])
    op.create_index('uq_fa_depr_reversal','fixed_asset_depreciations',['reversal_of_id'],unique=True,postgresql_where=sa.text('reversal_of_id IS NOT NULL'))
    op.drop_constraint('ck_fa_depr_accum_order','fixed_asset_depreciations',type_='check')
    op.create_check_constraint('ck_fa_depr_accum_order','fixed_asset_depreciations', '(reversal_of_id IS NULL AND accumulated_after >= accumulated_before) OR (reversal_of_id IS NOT NULL AND accumulated_after <= accumulated_before)')

def downgrade():
    # Refuse a destructive downgrade when new reversal history cannot fit the old schema.
    connection=op.get_bind()
    if connection.scalar(sa.text('SELECT count(*) FROM fixed_asset_depreciations WHERE reversal_of_id IS NOT NULL')):
        raise RuntimeError('Cannot downgrade depreciation with reversal history; no data was removed')
    op.drop_constraint('ck_fa_depr_accum_order','fixed_asset_depreciations',type_='check')
    op.create_check_constraint('ck_fa_depr_accum_order','fixed_asset_depreciations','accumulated_after >= accumulated_before')
    op.drop_index('uq_fa_depr_reversal',table_name='fixed_asset_depreciations')
    op.drop_index('ix_fa_depr_asset_period',table_name='fixed_asset_depreciations')
    op.create_unique_constraint('uq_fa_depr_asset_period','fixed_asset_depreciations',['company_id','fixed_asset_id','period_start','period_end'])
