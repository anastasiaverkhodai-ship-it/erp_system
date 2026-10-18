"""Recognize additional annual and parental social paid leave separately."""
from alembic import op
import sqlalchemy as sa
revision = '13d4a7b8c032'
down_revision = '13d4a7b8c031'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('leave_requests', 'leave_type', existing_type=sa.String(6), type_=sa.String(10), existing_nullable=False)
    op.drop_constraint('ck_leave_requests_type', 'leave_requests', type_='check')
    op.create_check_constraint('ck_leave_requests_type', 'leave_requests',
        "leave_type IN ('annual','additional','social','sick','unpaid','other')")


def downgrade():
    op.execute('LOCK TABLE leave_requests IN ACCESS EXCLUSIVE MODE')
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM leave_requests WHERE leave_type IN ('additional','social')) THEN RAISE EXCEPTION 'Paid leave category history must be preserved'; END IF; END $$")
    op.drop_constraint('ck_leave_requests_type', 'leave_requests', type_='check')
    op.create_check_constraint('ck_leave_requests_type', 'leave_requests',
        "leave_type IN ('annual','sick','unpaid','other')")
    op.alter_column('leave_requests', 'leave_type', existing_type=sa.String(10), type_=sa.String(6), existing_nullable=False)
