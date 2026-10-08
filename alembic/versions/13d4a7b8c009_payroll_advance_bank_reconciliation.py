"""Link advance payments to statement lines without losing reconciliation history.

Revision ID: 13d4a7b8c009
Revises: bb0425a7fbd6
"""
from alembic import op
import sqlalchemy as sa

revision = '13d4a7b8c009'
down_revision = 'bb0425a7fbd6'
branch_labels = None
depends_on = None

TABLES = (
    ('bank_statement_reconciliations', 'ck_bank_reconciliation_target',
     'fk_bank_reconciliation_advance'),
    ('bank_statement_reconciliation_active_links', 'ck_bank_reconciliation_link_target',
     'fk_bank_reconciliation_link_advance'),
)


def upgrade():
    for table, check, foreign_key in TABLES:
        op.add_column(table, sa.Column('payroll_advance_id', sa.Integer(), nullable=True))
        op.create_foreign_key(foreign_key, table, 'payroll_advances',
            ['company_id', 'payroll_advance_id'], ['company_id', 'id'], ondelete='RESTRICT')
        op.drop_constraint(check, table, type_='check')
        op.create_check_constraint(check, table,
            'num_nonnulls(payment_id, payroll_disbursement_id, payroll_advance_id) = 1')


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM bank_statement_reconciliations WHERE payroll_advance_id IS NOT NULL)
           OR EXISTS (SELECT 1 FROM bank_statement_reconciliation_active_links WHERE payroll_advance_id IS NOT NULL)
        THEN RAISE EXCEPTION 'Advance bank reconciliation history must be preserved';
        END IF;
    END $$""")
    for table, check, foreign_key in reversed(TABLES):
        op.drop_constraint(check, table, type_='check')
        op.drop_constraint(foreign_key, table, type_='foreignkey')
        op.drop_column(table, 'payroll_advance_id')
        op.create_check_constraint(check, table, 'num_nonnulls(payment_id, payroll_disbursement_id) = 1')
