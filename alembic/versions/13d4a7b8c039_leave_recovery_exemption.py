"""Preserve the legal exemption from recovering unearned annual leave."""
from alembic import op
import sqlalchemy as sa
revision='13d4a7b8c039'
down_revision='13d4a7b8c038'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('payroll_leave_compensations',sa.Column('recovery_exemption',sa.JSON(none_as_null=True),nullable=True))


def downgrade():
    op.execute('LOCK TABLE payroll_leave_compensations IN ACCESS EXCLUSIVE MODE')
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM payroll_leave_compensations WHERE recovery_exemption IS NOT NULL)
        THEN RAISE EXCEPTION 'Leave recovery exemption evidence must be preserved'; END IF;
    END $$""")
    op.drop_column('payroll_leave_compensations','recovery_exemption')
