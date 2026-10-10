"""Store minimum-wage assessment and a separate guarantee earning.

Revision ID: 13d4a7b8c015
Revises: 13d4a7b8c014
"""
from alembic import op
import sqlalchemy as sa
revision='13d4a7b8c015'
down_revision='13d4a7b8c014'
branch_labels=None
depends_on=None


def checks(include):
    kinds="'salary','supplement','vacation_pay','sick_pay'" + (",'minimum_wage'" if include else '')
    for name,expr in {
        'type':f"line_type IN ({kinds},'manual_adjustment')",
        'rate':f"(line_type IN ({kinds}) AND rate IS NOT NULL AND rate >= 0) OR (line_type = 'manual_adjustment' AND rate IS NOT NULL)",
        'amount':f"(line_type IN ({kinds}) AND amount >= 0) OR line_type = 'manual_adjustment'",
    }.items():
        constraint='ck_payroll_calculation_lines_'+name
        op.drop_constraint(constraint,'payroll_calculation_lines',type_='check')
        op.create_check_constraint(constraint,'payroll_calculation_lines',expr)


def upgrade():
    op.add_column('payroll_calculations',sa.Column('minimum_wage_assessment',sa.JSON(),nullable=True))
    checks(True)


def downgrade():
    op.execute("""DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM payroll_calculations WHERE minimum_wage_assessment IS NOT NULL)
       OR EXISTS (SELECT 1 FROM payroll_calculation_lines WHERE line_type='minimum_wage') THEN
      RAISE EXCEPTION 'Cannot downgrade minimum wage with recorded assessments';
    END IF; END $$""")
    checks(False)
    op.drop_column('payroll_calculations','minimum_wage_assessment')
