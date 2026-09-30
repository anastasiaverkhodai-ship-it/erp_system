"""Preserve commissioning source allocations for historical FA reports."""
from alembic import op
import sqlalchemy as sa
revision = "8fa12512c001"
down_revision = "8fa12510c004"
branch_labels = None
depends_on = None


def upgrade():
    # Do not invent historical allocations from the journal being reconciled.
    op.add_column("fixed_asset_commissionings", sa.Column("cost_snapshot", sa.JSON(), nullable=True))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM fixed_asset_commissionings WHERE cost_snapshot IS NOT NULL")):
        raise RuntimeError("Cannot remove commissioning source history; no data was removed")
    op.drop_column("fixed_asset_commissionings", "cost_snapshot")
