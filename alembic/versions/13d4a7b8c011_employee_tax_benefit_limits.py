"""Snapshot monthly employee income and explicit social-benefit eligibility."""
from alembic import op
import sqlalchemy as sa
revision='13d4a7b8c011'
down_revision='13d4a7b8c010'
branch_labels=None
depends_on=None

COLUMNS={
    'payroll_employee_tax_profiles': ('benefit_amount_override','benefit_income_limit_override'),
    'payroll_statutory_base_rules': ('benefit_income_limit',),
    'payroll_statutory_result_lines': ('employee_gross_amount','benefit_income_limit_applied'),
}


def upgrade():
    for table,columns in COLUMNS.items():
        for column in columns:
            op.add_column(table,sa.Column(column,sa.Numeric(18,2),nullable=True))
    op.create_check_constraint('ck_payroll_profile_benefit_override','payroll_employee_tax_profiles',
        "(benefit_amount_override IS NULL AND benefit_income_limit_override IS NULL) OR (benefit_amount_override IS NOT NULL AND benefit_income_limit_override IS NOT NULL AND benefit_amount_override >= 0 AND benefit_income_limit_override > 0 AND category = 'benefit_eligible')")
    op.create_check_constraint('ck_payroll_rule_benefit_income_limit','payroll_statutory_base_rules',
        'benefit_income_limit IS NULL OR benefit_income_limit > 0')


def downgrade():
    for table,columns in COLUMNS.items():
        conditions=' OR '.join(column+' IS NOT NULL' for column in columns)
        op.execute(f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM {table} WHERE {conditions}) THEN RAISE EXCEPTION 'Employee tax benefit history must be preserved'; END IF; END $$")
    op.drop_constraint('ck_payroll_profile_benefit_override','payroll_employee_tax_profiles',type_='check')
    op.drop_constraint('ck_payroll_rule_benefit_income_limit','payroll_statutory_base_rules',type_='check')
    for table,columns in reversed(tuple(COLUMNS.items())):
        for column in reversed(columns):
            op.drop_column(table,column)
