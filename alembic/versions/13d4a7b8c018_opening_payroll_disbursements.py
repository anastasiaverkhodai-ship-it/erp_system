"""Allow existing disbursement lifecycle to settle opening net payroll debt."""
from alembic import op
import sqlalchemy as sa
revision = '13d4a7b8c018'
down_revision = '13d4a7b8c017'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('payroll_disbursements', sa.Column('opening_debt_id', sa.Integer(), nullable=True))
    op.alter_column('payroll_disbursements', 'payroll_calculation_id', existing_type=sa.Integer(), nullable=True)
    op.create_foreign_key('fk_payroll_disbursements_opening_debt', 'payroll_disbursements',
        'payroll_opening_debts', ['company_id', 'opening_debt_id'], ['company_id', 'id'], ondelete='RESTRICT')
    op.create_check_constraint('ck_payroll_disbursements_source', 'payroll_disbursements',
        'num_nonnulls(payroll_calculation_id, opening_debt_id) = 1')


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM payroll_disbursements WHERE opening_debt_id IS NOT NULL) THEN
            RAISE EXCEPTION 'Opening payroll disbursement history must be preserved';
        END IF;
    END $$""")
    op.drop_constraint('ck_payroll_disbursements_source', 'payroll_disbursements', type_='check')
    op.drop_constraint('fk_payroll_disbursements_opening_debt', 'payroll_disbursements', type_='foreignkey')
    op.alter_column('payroll_disbursements', 'payroll_calculation_id', existing_type=sa.Integer(), nullable=False)
    op.drop_column('payroll_disbursements', 'opening_debt_id')
