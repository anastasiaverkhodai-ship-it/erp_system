"""Explicit no-history basis for first-day incapacity."""
from alembic import op
import sqlalchemy as sa
revision='13d4a7b8c019'
down_revision='13d4a7b8c018'
branch_labels=None
depends_on=None
REFERENCE_CHECK="((reference_period_start IS NOT NULL AND reference_period_end IS NOT NULL AND reference_period_end >= reference_period_start) OR (reference_period_start IS NULL AND reference_period_end IS NULL AND rule_version = '3-2026-first-day-monthly'))"
SOURCE_CHECK="source_type IN ('earnings_history','payroll_calculation','insurance_service_evidence','replacement_salary_rate')"


def upgrade():
    op.drop_constraint('ck_payroll_sick_leave_calculation_reference_period','payroll_sick_leave_calculations',type_='check')
    for name in ('reference_period_start','reference_period_end'):
        op.alter_column('payroll_sick_leave_calculations',name,existing_type=sa.Date(),nullable=True)
    op.create_check_constraint('ck_payroll_sick_leave_calculation_reference_period','payroll_sick_leave_calculations',REFERENCE_CHECK)
    op.drop_constraint('ck_payroll_sick_leave_source_type','payroll_sick_leave_calculation_sources',type_='check')
    op.create_check_constraint('ck_payroll_sick_leave_source_type','payroll_sick_leave_calculation_sources',SOURCE_CHECK)


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM payroll_sick_leave_calculations WHERE reference_period_start IS NULL)
        OR EXISTS (SELECT 1 FROM payroll_sick_leave_calculation_sources WHERE source_type='replacement_salary_rate') THEN
            RAISE EXCEPTION 'First-day sick salary history must be preserved';
        END IF;
    END $$""")
    op.drop_constraint('ck_payroll_sick_leave_calculation_reference_period','payroll_sick_leave_calculations',type_='check')
    for name in ('reference_period_start','reference_period_end'):
        op.alter_column('payroll_sick_leave_calculations',name,existing_type=sa.Date(),nullable=False)
    op.create_check_constraint('ck_payroll_sick_leave_calculation_reference_period','payroll_sick_leave_calculations','reference_period_end >= reference_period_start')
    op.drop_constraint('ck_payroll_sick_leave_source_type','payroll_sick_leave_calculation_sources',type_='check')
    op.create_check_constraint('ck_payroll_sick_leave_source_type','payroll_sick_leave_calculation_sources',"source_type IN ('earnings_history','payroll_calculation','insurance_service_evidence')")
