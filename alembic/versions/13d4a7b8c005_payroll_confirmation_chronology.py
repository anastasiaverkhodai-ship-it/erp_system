"""Preserve independent payroll payout recognition and reversal dates."""
from alembic import op
import sqlalchemy as sa

revision = '13d4a7b8c005'
down_revision = '13d4a7b8c004'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('payroll_disbursements', sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('payroll_disbursements', sa.Column('reversed_on', sa.Date(), nullable=True))
    op.execute("""UPDATE payroll_disbursements p SET confirmed_at = j.posted_at AT TIME ZONE 'UTC'
        FROM journal_entries j WHERE j.company_id = p.company_id
        AND j.payroll_disbursement_id = p.id AND j.reversal_of_id IS NULL
        AND j.status IN ('posted', 'reversed')""")
    op.execute("""DO $$ BEGIN IF EXISTS (
        SELECT 1 FROM payroll_disbursements p JOIN journal_entries j
        ON j.company_id=p.company_id AND j.payroll_disbursement_id=p.id
        WHERE j.status IN ('posted','reversed') AND p.confirmed_at IS NULL
        ) THEN RAISE EXCEPTION 'Posted payroll lacks confirmation timestamp'; END IF; END $$""")
    op.execute("""UPDATE payroll_disbursements p SET reversed_on = j.entry_date
        FROM journal_entries j WHERE j.company_id = p.company_id
        AND j.payroll_disbursement_id = p.id AND j.reversal_of_id IS NOT NULL
        AND j.status = 'posted'""")
    op.create_check_constraint('ck_payroll_disbursement_chronology', 'payroll_disbursements',
                               'reversed_on IS NULL OR confirmed_at IS NOT NULL')


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM payroll_disbursements
        WHERE confirmed_at IS NOT NULL OR reversed_on IS NOT NULL)
        THEN RAISE EXCEPTION 'Payroll chronology must be preserved'; END IF; END $$""")
    op.drop_constraint('ck_payroll_disbursement_chronology', 'payroll_disbursements', type_='check')
    op.drop_column('payroll_disbursements', 'reversed_on')
    op.drop_column('payroll_disbursements', 'confirmed_at')
