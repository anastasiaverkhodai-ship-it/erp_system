"""Preserve documented salary substitution for vacation averages."""
from alembic import op
import sqlalchemy as sa
revision = '13d4a7b8c036'
down_revision = '13d4a7b8c035'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('payroll_vacation_calculation_sources', sa.Column('basis_snapshot', sa.JSON(none_as_null=True), nullable=True))
    op.drop_constraint('ck_payroll_vacation_source_type', 'payroll_vacation_calculation_sources', type_='check')
    op.create_check_constraint('ck_payroll_vacation_source_type', 'payroll_vacation_calculation_sources',
        "source_type IN ('earnings_history','payroll_calculation','leave_opening','salary_substitution')")

    op.create_check_constraint('ck_pvcs_salary_basis', 'payroll_vacation_calculation_sources', "(source_type = 'salary_substitution') = (basis_snapshot IS NOT NULL)")


def downgrade():
    op.execute('LOCK TABLE payroll_vacation_calculation_sources IN ACCESS EXCLUSIVE MODE')
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM payroll_vacation_calculation_sources WHERE source_type='salary_substitution' OR basis_snapshot IS NOT NULL) THEN RAISE EXCEPTION 'Salary substitution history must be preserved'; END IF; END $$")
    op.drop_constraint('ck_payroll_vacation_source_type', 'payroll_vacation_calculation_sources', type_='check')
    op.create_check_constraint('ck_payroll_vacation_source_type', 'payroll_vacation_calculation_sources',
        "source_type IN ('earnings_history','payroll_calculation','leave_opening')")
    op.drop_constraint('ck_pvcs_salary_basis','payroll_vacation_calculation_sources',type_='check')
    op.drop_column('payroll_vacation_calculation_sources', 'basis_snapshot')
