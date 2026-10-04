"""Idempotent partial payroll disbursements without rewriting existing amounts.
Revision ID: 13d4a7b8c003
Revises: 13d4a7b8c002
"""
from alembic import op
import sqlalchemy as sa
revision='13d4a7b8c003'
down_revision='13d4a7b8c002'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('payroll_disbursements',sa.Column('cancelled_at',sa.DateTime(timezone=True),nullable=True))
    op.add_column('payroll_disbursements',sa.Column('cancelled_by',sa.Integer(),nullable=True))
    op.create_foreign_key('payroll_disbursements_cancelled_by_fkey','payroll_disbursements','users',['cancelled_by'],['id'],ondelete='RESTRICT')
    op.create_check_constraint('ck_payroll_disbursement_cancellation','payroll_disbursements','(cancelled_at IS NULL) = (cancelled_by IS NULL)')
    op.add_column('payroll_disbursements',sa.Column('request_key',sa.String(200),nullable=True))
    op.execute("UPDATE payroll_disbursements SET request_key='payroll:' || payroll_calculation_id::text")
    op.alter_column('payroll_disbursements','request_key',nullable=False)
    op.drop_constraint('uq_payroll_disbursements_company_calculation','payroll_disbursements',type_='unique')
    op.create_unique_constraint('uq_payroll_disbursements_company_request','payroll_disbursements',['company_id','request_key'])
    op.create_check_constraint('ck_payroll_disbursement_positive','payroll_disbursements','amount > 0')


def downgrade():
    # Request keys are the retry contract. Losing even one would change behavior.
    check="SELECT EXISTS (SELECT 1 FROM payroll_disbursements WHERE cancelled_at IS NOT NULL OR request_key <> 'payroll:' || payroll_calculation_id::text)"
    if op.get_context().as_sql:
        op.execute("DO $$ BEGIN IF ("+check+") THEN RAISE EXCEPTION 'Partial disbursement history must be preserved'; END IF; END $$")
    elif op.get_bind().scalar(sa.text(check)):
        raise RuntimeError('Partial disbursement history must be preserved')
    op.drop_constraint('ck_payroll_disbursement_positive','payroll_disbursements',type_='check')
    op.drop_constraint('uq_payroll_disbursements_company_request','payroll_disbursements',type_='unique')
    op.create_unique_constraint('uq_payroll_disbursements_company_calculation','payroll_disbursements',['company_id','payroll_calculation_id'])
    op.drop_column('payroll_disbursements','request_key')
    op.drop_constraint('ck_payroll_disbursement_cancellation','payroll_disbursements',type_='check')
    op.drop_constraint('payroll_disbursements_cancelled_by_fkey','payroll_disbursements',type_='foreignkey')
    op.drop_column('payroll_disbursements','cancelled_by')
    op.drop_column('payroll_disbursements','cancelled_at')
