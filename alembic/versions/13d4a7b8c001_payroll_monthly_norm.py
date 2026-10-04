"""Preserve the contractual full-month payroll denominator.

Revision ID: 13d4a7b8c001
Revises: ae1b3f877577
"""
from alembic import op
import sqlalchemy as sa

revision = '13d4a7b8c001'
down_revision = 'ae1b3f877577'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('payroll_inputs', sa.Column('monthly_norm_minutes', sa.Integer(), nullable=True))


def downgrade():
    if op.get_context().as_sql:
        op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM payroll_inputs WHERE monthly_norm_minutes IS NOT NULL) THEN RAISE EXCEPTION 'Payroll norm snapshots must be preserved'; END IF; END $$")
    elif op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM payroll_inputs WHERE monthly_norm_minutes IS NOT NULL)')):
        raise RuntimeError('Payroll norm snapshots must be preserved')
    op.drop_column('payroll_inputs', 'monthly_norm_minutes')
