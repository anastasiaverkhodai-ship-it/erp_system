"""Documented employment events; no invented historical orders."""
from alembic import op
import sqlalchemy as sa
revision='13d4a7b8c006'
down_revision='13d4a7b8c005'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('employment_events',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('company_id',sa.Integer(),sa.ForeignKey('companies.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('employment_contract_id',sa.Integer(),nullable=False),
        sa.Column('event_type',sa.String(20),nullable=False),
        sa.Column('effective_date',sa.Date(),nullable=False),
        sa.Column('order_number',sa.String(100),nullable=False),
        sa.Column('order_date',sa.Date(),nullable=False),
        sa.Column('reason',sa.String(1000),nullable=False),
        sa.Column('request_key',sa.String(200),nullable=False),
        sa.Column('before_state',sa.JSON(),nullable=False),
        sa.Column('after_state',sa.JSON(),nullable=False),
        sa.Column('request_payload',sa.JSON(),nullable=False),
        sa.Column('reversal_of_id',sa.Integer(),nullable=True),
        sa.Column('created_by',sa.Integer(),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.UniqueConstraint('company_id','id',name='uq_employment_events_company_id'),
        sa.UniqueConstraint('company_id','request_key',name='uq_employment_events_request'),
        sa.UniqueConstraint('reversal_of_id',name='uq_employment_events_reversal'),
        sa.ForeignKeyConstraint(['company_id','employment_contract_id'],['employment_contracts.company_id','employment_contracts.id'],name='fk_employment_events_contract',ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['company_id','reversal_of_id'],['employment_events.company_id','employment_events.id'],name='fk_employment_events_reversal',ondelete='RESTRICT'),
        sa.CheckConstraint("event_type IN ('hire','transfer','termination','reversal')",name='ck_employment_events_type'),
        sa.CheckConstraint("(event_type = 'reversal') = (reversal_of_id IS NOT NULL)",name='ck_employment_events_reversal'),
        sa.CheckConstraint("length(trim(order_number)) > 0 AND length(trim(reason)) > 0 AND length(trim(request_key)) > 0",name='ck_employment_events_evidence'),
    )
    op.create_index('ix_employment_events_timeline','employment_events',['company_id','employment_contract_id','effective_date','id'])
    op.execute("""CREATE FUNCTION reject_employment_event_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Employment events are immutable; append a correction'; END; $$""")
    op.execute('CREATE TRIGGER employment_events_immutable BEFORE UPDATE OR DELETE ON employment_events FOR EACH ROW EXECUTE FUNCTION reject_employment_event_mutation()')


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM employment_events)
        THEN RAISE EXCEPTION 'Employment event history must be preserved'; END IF; END $$""")
    op.drop_table('employment_events')
    op.execute('DROP FUNCTION reject_employment_event_mutation()')
