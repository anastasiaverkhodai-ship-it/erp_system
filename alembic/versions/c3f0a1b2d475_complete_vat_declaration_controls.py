"""Allow signed period corrections and enforce declaration reconciliation."""
from alembic import op
revision='c3f0a1b2d475'
down_revision='b2e9f0a1c364'
branch_labels=None
depends_on=None
OLD="output_taxable_base >= 0 AND output_vat >= 0 AND input_taxable_base >= 0 AND input_vat_credit >= 0 AND opening_negative_carry >= 0 AND vat_payable >= 0 AND current_period_negative >= 0 AND closing_negative_carry >= 0"
NEW="opening_negative_carry >= 0 AND vat_payable >= 0 AND current_period_negative >= 0 AND closing_negative_carry >= 0"
def upgrade():
    op.drop_constraint('ck_vd_amounts_nonneg','vat_declarations',type_='check')
    op.create_check_constraint('ck_vd_amounts_nonneg','vat_declarations',NEW)
    op.create_check_constraint('ck_vd_net_balance','vat_declarations','vat_payable - closing_negative_carry = output_vat - input_vat_credit - opening_negative_carry')
def downgrade():
    op.drop_constraint('ck_vd_net_balance','vat_declarations',type_='check')
    op.drop_constraint('ck_vd_amounts_nonneg','vat_declarations',type_='check')
    op.create_check_constraint('ck_vd_amounts_nonneg','vat_declarations',OLD)
