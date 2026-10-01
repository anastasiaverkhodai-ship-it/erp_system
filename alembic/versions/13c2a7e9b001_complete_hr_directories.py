"""Complete HR directories: source history, payment details and scoped access."""
from alembic import op
import sqlalchemy as sa

revision = '13c2a7e9b001'
down_revision = '1bace2452a82'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('employment_contracts', 'status', existing_type=sa.String(9), server_default='active')
    op.create_check_constraint('ck_employment_contracts_status', 'employment_contracts', "status IN ('active','ended','cancelled')")
    op.create_check_constraint('ck_employment_contracts_arrangement', 'employment_contracts', "work_arrangement IN ('full_time','part_time')")
    op.add_column('employees', sa.Column('payment_iban', sa.String(29), nullable=True))
    op.create_table('hr_changes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('entity_type', sa.String(30), nullable=False),
        sa.Column('entity_id', sa.Integer(), nullable=False),
        sa.Column('before_state', sa.JSON(), nullable=True),
        sa.Column('after_state', sa.JSON(), nullable=False),
        sa.Column('changed_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=True),
        sa.Column('changed_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("entity_type IN ('employee','department','position','employment_contract')", name='ck_hr_changes_entity_type'))
    op.create_index('ix_hr_changes_entity', 'hr_changes', ['company_id', 'entity_type', 'entity_id', 'id'])
    # Capture existing state as a migration baseline, never invent a human actor.
    for table, entity in [('employees','employee'), ('departments','department'), ('positions','position'), ('employment_contracts','employment_contract')]:
        op.execute(sa.text(f"INSERT INTO hr_changes(company_id,entity_type,entity_id,after_state) SELECT company_id,'{entity}',id,to_jsonb(t)-'created_at'-'updated_at' FROM {table} t"))
    op.execute("INSERT INTO permissions(name) VALUES ('employees.read'),('employees.manage') ON CONFLICT(name) DO NOTHING")
    op.execute("INSERT INTO roles(name) VALUES ('hr_manager') ON CONFLICT(name) DO NOTHING")
    op.execute("INSERT INTO role_permissions(role_id,permission_id) SELECT r.id,p.id FROM roles r CROSS JOIN permissions p WHERE r.name IN ('admin','hr_manager') AND p.name IN ('employees.read','employees.manage') ON CONFLICT DO NOTHING")


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text('SELECT EXISTS(SELECT 1 FROM hr_changes) OR EXISTS(SELECT 1 FROM employees WHERE payment_iban IS NOT NULL)')):
        raise RuntimeError('Cannot discard HR history or payment details; no data was removed')
    op.drop_index('ix_hr_changes_entity', table_name='hr_changes')
    op.drop_table('hr_changes')
    op.drop_column('employees', 'payment_iban')
    op.drop_constraint('ck_employment_contracts_arrangement', 'employment_contracts', type_='check')
    op.drop_constraint('ck_employment_contracts_status', 'employment_contracts', type_='check')
    op.alter_column('employment_contracts', 'status', existing_type=sa.String(9), server_default='draft')
    # Keep provisioned permissions/roles: other deployments may already use them.
