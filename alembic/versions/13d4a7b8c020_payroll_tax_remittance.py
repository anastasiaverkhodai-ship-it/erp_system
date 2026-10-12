"""Attribute posted bank tax payments to recognized payroll liabilities."""
from alembic import op
import sqlalchemy as sa
revision='13d4a7b8c020'
down_revision='13d4a7b8c019'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('payroll_tax_remittance',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('company_id',sa.Integer(),sa.ForeignKey('companies.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('journal_entry_id',sa.Integer(),nullable=False),
        sa.Column('bank_account_id',sa.Integer(),nullable=False),
        sa.Column('component',sa.String(64),nullable=False),
        sa.Column('amount',sa.Numeric(18,2),nullable=False),
        sa.Column('request_key',sa.String(200),nullable=False),
        sa.Column('request_fingerprint',sa.String(64),nullable=False),
        sa.Column('document_reference',sa.String(500),nullable=False),
        sa.Column('budget_payment_reference',sa.String(500),nullable=False),
        sa.Column('created_by',sa.Integer(),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.UniqueConstraint('company_id','id',name='uq_payroll_tax_remittance_company'),
        sa.UniqueConstraint('company_id','request_key',name='uq_payroll_tax_remittance_request'),
        sa.UniqueConstraint('company_id','journal_entry_id',name='uq_payroll_tax_remittance_journal'),
        sa.ForeignKeyConstraint(['company_id','journal_entry_id'],['journal_entries.company_id','journal_entries.id'],name='fk_payroll_tax_remittance_journal',ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['company_id','bank_account_id'],['bank_accounts.company_id','bank_accounts.id'],name='fk_payroll_tax_remittance_bank',ondelete='RESTRICT'),
        sa.CheckConstraint("component IN ('personal_income_tax','military_levy','unified_social_contribution')",name='ck_payroll_tax_remittance_component'),
        sa.CheckConstraint('amount > 0',name='ck_payroll_tax_remittance_amount'),
        sa.CheckConstraint('length(trim(document_reference)) > 0 AND length(trim(budget_payment_reference)) > 0',name='ck_payroll_tax_remittance_references'))
    op.create_table('payroll_tax_remittance_lines',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('company_id',sa.Integer(),nullable=False),
        sa.Column('remittance_id',sa.Integer(),nullable=False),
        sa.Column('payroll_calculation_id',sa.Integer(),nullable=False),
        sa.Column('amount',sa.Numeric(18,2),nullable=False),
        sa.UniqueConstraint('company_id','remittance_id','payroll_calculation_id',name='uq_payroll_tax_remittance_line_source'),
        sa.ForeignKeyConstraint(['company_id','remittance_id'],['payroll_tax_remittance.company_id','payroll_tax_remittance.id'],name='fk_payroll_tax_remittance_line_parent',ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['company_id','payroll_calculation_id'],['payroll_calculations.company_id','payroll_calculations.id'],name='fk_payroll_tax_remittance_line_calculation',ondelete='RESTRICT'),
        sa.CheckConstraint('amount > 0',name='ck_payroll_tax_remittance_line_amount'))


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM payroll_tax_remittance) THEN
            RAISE EXCEPTION 'Cannot discard tax remittance history';
        END IF;
    END $$""")
    op.drop_table('payroll_tax_remittance_lines')
    op.drop_table('payroll_tax_remittance')
