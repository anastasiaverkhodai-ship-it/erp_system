"""Preserve advance calculation sources and dated individual statutory rates."""
from alembic import op
import sqlalchemy as sa
revision = '13d4a7b8c010'
down_revision = '13d4a7b8c009'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('payroll_advances', sa.Column('calculation_snapshot_json', sa.Text(), nullable=True))
    op.add_column('payroll_statutory_rates', sa.Column('employee_id', sa.Integer(), nullable=True))
    op.add_column('payroll_statutory_rates', sa.Column('source_reference', sa.String(500), nullable=True))
    op.create_foreign_key('fk_payroll_statutory_rate_employee', 'payroll_statutory_rates', 'employees',
        ['company_id','employee_id'], ['company_id','id'], ondelete='RESTRICT')
    op.create_check_constraint('ck_payroll_statutory_rate_evidence', 'payroll_statutory_rates',
        'employee_id IS NULL OR (source_reference IS NOT NULL AND length(trim(source_reference)) > 0)')


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM payroll_advances WHERE calculation_snapshot_json IS NOT NULL)
           OR EXISTS (SELECT 1 FROM payroll_statutory_rates WHERE employee_id IS NOT NULL OR source_reference IS NOT NULL)
        THEN RAISE EXCEPTION 'Advance and statutory rate history must be preserved'; END IF;
    END $$""")
    op.drop_constraint('ck_payroll_statutory_rate_evidence', 'payroll_statutory_rates', type_='check')
    op.drop_constraint('fk_payroll_statutory_rate_employee', 'payroll_statutory_rates', type_='foreignkey')
    op.drop_column('payroll_statutory_rates', 'source_reference')
    op.drop_column('payroll_statutory_rates', 'employee_id')
    op.drop_column('payroll_advances', 'calculation_snapshot_json')
