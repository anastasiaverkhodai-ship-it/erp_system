"""Preserve documented authorization for annual leave taken in advance."""
from alembic import op
import sqlalchemy as sa
revision='13d4a7b8c038'
down_revision='13d4a7b8c037'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('payroll_leave_balance_entries',sa.Column('advance_basis',sa.JSON(none_as_null=True),nullable=True))
    op.create_check_constraint('ck_plbe_advance_kind','payroll_leave_balance_entries',"advance_basis IS NULL OR kind='use'")


def downgrade():
    op.execute('LOCK TABLE payroll_leave_balance_entries IN ACCESS EXCLUSIVE MODE')
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM payroll_leave_balance_entries WHERE advance_basis IS NOT NULL)
        THEN RAISE EXCEPTION 'Advance leave evidence must be preserved'; END IF;
    END $$""")
    op.drop_constraint('ck_plbe_advance_kind','payroll_leave_balance_entries',type_='check')
    op.drop_column('payroll_leave_balance_entries','advance_basis')
