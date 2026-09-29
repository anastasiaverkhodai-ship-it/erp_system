"""Persist planned and actual output for monthly production depreciation."""
from alembic import op
import sqlalchemy as sa

revision = '8fa12510c002'
down_revision = '8fa12510c001'
branch_labels = None
depends_on = None


def upgrade():
    for table in ('fixed_assets', 'fixed_asset_card_history', 'fixed_asset_depreciations'):
        op.add_column(table, sa.Column('expected_output', sa.Numeric(18, 6), nullable=True))
    op.add_column('fixed_asset_depreciations', sa.Column('actual_output', sa.Numeric(18, 6), nullable=True))
    op.create_check_constraint('ck_fa_expected_output_positive', 'fixed_assets', 'expected_output IS NULL OR expected_output > 0')
    op.create_check_constraint('ck_fa_depr_output_nonnegative', 'fixed_asset_depreciations', 'actual_output IS NULL OR actual_output >= 0')
    op.create_check_constraint('ck_fa_depr_expected_positive', 'fixed_asset_depreciations', 'expected_output IS NULL OR expected_output > 0')
    op.drop_constraint('ck_fa_depr_amount_positive', 'fixed_asset_depreciations', type_='check')
    op.create_check_constraint('ck_fa_depr_amount_positive', 'fixed_asset_depreciations', "amount > 0 OR (amount = 0 AND method = 'production' AND actual_output IS NOT NULL)")


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM fixed_asset_depreciations WHERE actual_output IS NOT NULL')):
        raise RuntimeError('Cannot remove recorded production output; no data was removed')
    for table in ('fixed_assets', 'fixed_asset_card_history'):
        if op.get_bind().scalar(sa.text(f'SELECT count(*) FROM {table} WHERE expected_output IS NOT NULL')):
            raise RuntimeError('Cannot remove planned production output; no data was removed')
    op.drop_constraint('ck_fa_depr_amount_positive', 'fixed_asset_depreciations', type_='check')
    op.create_check_constraint('ck_fa_depr_amount_positive', 'fixed_asset_depreciations', 'amount > 0')
    op.drop_constraint('ck_fa_depr_expected_positive', 'fixed_asset_depreciations', type_='check')
    op.drop_constraint('ck_fa_depr_output_nonnegative', 'fixed_asset_depreciations', type_='check')
    op.drop_constraint('ck_fa_expected_output_positive', 'fixed_assets', type_='check')
    op.drop_column('fixed_asset_depreciations', 'actual_output')
    for table in ('fixed_assets', 'fixed_asset_card_history', 'fixed_asset_depreciations'):
        op.drop_column(table, 'expected_output')
