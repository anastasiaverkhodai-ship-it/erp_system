"""Persist prospective useful-life changes on improvements."""
from alembic import op
import sqlalchemy as sa
revision = '8fa12510c004'
down_revision = '8fa12510c003'
branch_labels = None
depends_on = None

def upgrade():
    for name in ('new_remaining_life_months','useful_life_months_before'):
        op.add_column('fixed_asset_repair_improvements',sa.Column(name,sa.Integer(),nullable=True))
    for name in ('depreciable_base_after','original_cost_after','accumulated_at_change'):
        op.add_column('fixed_asset_repair_improvements',sa.Column(name,sa.Numeric(18,2),nullable=True))
    op.create_check_constraint('ck_fari_remaining_life','fixed_asset_repair_improvements','new_remaining_life_months IS NULL OR new_remaining_life_months > 0')

def downgrade():
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM fixed_asset_repair_improvements WHERE new_remaining_life_months IS NOT NULL')):
        raise RuntimeError('Cannot remove useful-life change history; no data was removed')
    op.drop_constraint('ck_fari_remaining_life','fixed_asset_repair_improvements',type_='check')
    for name in ('new_remaining_life_months','useful_life_months_before','depreciable_base_after','original_cost_after','accumulated_at_change'):
        op.drop_column('fixed_asset_repair_improvements',name)
