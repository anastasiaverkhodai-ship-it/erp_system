"""Persist prospective payroll paid-income attribution without changing legacy data."""
from alembic import op
import sqlalchemy as sa
revision = 'p13paidincome261115'
down_revision = 'p13receiptread261109'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("companies", sa.Column("salary_payment_day", sa.Integer(), nullable=False, server_default="7"))
    op.create_check_constraint("ck_company_salary_payment_day", "companies", "salary_payment_day BETWEEN 1 AND 7")
    op.create_table('payroll_paid_income',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('company_id',sa.Integer(),nullable=False),
        sa.Column('payroll_disbursement_id',sa.Integer(),nullable=False),
        sa.Column('journal_entry_id',sa.Integer(),nullable=False),
        sa.Column('snapshot_json',sa.Text(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.UniqueConstraint('company_id','payroll_disbursement_id',name='uq_paid_income_payment'),
        sa.ForeignKeyConstraint(['company_id','payroll_disbursement_id'],['payroll_disbursements.company_id','payroll_disbursements.id'],name='fk_paid_income_payment',ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['company_id','journal_entry_id'],['journal_entries.company_id','journal_entries.id'],name='fk_paid_income_journal',ondelete='RESTRICT'))
    op.execute("""CREATE FUNCTION payroll_paid_income_immutable() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      RAISE EXCEPTION 'Posted paid-income attribution is immutable';
    END $$""")
    op.execute('CREATE TRIGGER trg_paid_income_immutable BEFORE UPDATE OR DELETE ON payroll_paid_income FOR EACH ROW EXECUTE FUNCTION payroll_paid_income_immutable()')


def downgrade():
    op.drop_constraint("ck_company_salary_payment_day", "companies", type_="check")
    op.drop_column("companies", "salary_payment_day")
    op.drop_table('payroll_paid_income')
    op.execute('DROP FUNCTION payroll_paid_income_immutable()')
