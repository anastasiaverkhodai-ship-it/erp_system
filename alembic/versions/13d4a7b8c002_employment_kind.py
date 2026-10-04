"""Distinguish primary and secondary employment.
Revision ID: 13d4a7b8c002
Revises: 13d4a7b8c001
"""
from alembic import op
import sqlalchemy as sa
revision='13d4a7b8c002'
down_revision='13d4a7b8c001'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('employment_contracts',sa.Column('employment_kind',sa.String(32),nullable=True))
    op.create_check_constraint('ck_employment_contracts_kind','employment_contracts',"employment_kind IN ('primary','internal_secondary','external_secondary')")


def downgrade():
    if op.get_context().as_sql:
        op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM employment_contracts WHERE employment_kind <> 'primary') THEN RAISE EXCEPTION 'Secondary employment must be preserved'; END IF; END $$")
    elif op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM employment_contracts WHERE employment_kind <> 'primary')")):
        raise RuntimeError('Secondary employment must be preserved')
    op.drop_constraint('ck_employment_contracts_kind','employment_contracts',type_='check')
    op.drop_column('employment_contracts','employment_kind')
