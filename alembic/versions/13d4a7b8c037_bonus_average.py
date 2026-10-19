"""Document and preserve performance-bonus average allocation."""
from alembic import op
import sqlalchemy as sa

revision = '13d4a7b8c037'
down_revision = '13d4a7b8c036'
branch_labels = None
depends_on = None


def source_checks(with_bonus):
    op.drop_constraint('ck_payroll_vacation_source_type', 'payroll_vacation_calculation_sources', type_='check')
    op.drop_constraint('ck_pvcs_salary_basis', 'payroll_vacation_calculation_sources', type_='check')
    kinds = "'earnings_history','payroll_calculation','leave_opening','salary_substitution'"
    snapshot_kinds = "'salary_substitution'"
    if with_bonus:
        kinds += ",'bonus_allocation'"
        snapshot_kinds += ",'bonus_allocation'"
    op.create_check_constraint('ck_payroll_vacation_source_type',
        'payroll_vacation_calculation_sources', f"source_type IN ({kinds})")
    op.create_check_constraint('ck_pvcs_salary_basis',
        'payroll_vacation_calculation_sources',
        f"(source_type IN ({snapshot_kinds})) = (basis_snapshot IS NOT NULL)")


def upgrade():
    op.add_column('payroll_supplements', sa.Column('average_basis', sa.JSON(none_as_null=True), nullable=True))
    op.create_check_constraint('ck_payroll_bonus_basis_kind', 'payroll_supplements',
                               "average_basis IS NULL OR kind = 'bonus'")
    source_checks(True)


def downgrade():
    op.execute('LOCK TABLE payroll_supplements, payroll_vacation_calculation_sources IN ACCESS EXCLUSIVE MODE')
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM payroll_supplements WHERE average_basis IS NOT NULL)
           OR EXISTS (SELECT 1 FROM payroll_vacation_calculation_sources WHERE source_type='bonus_allocation')
        THEN RAISE EXCEPTION 'Bonus average evidence must be preserved'; END IF;
    END $$""")
    source_checks(False)
    op.drop_constraint('ck_payroll_bonus_basis_kind', 'payroll_supplements', type_='check')
    op.drop_column('payroll_supplements', 'average_basis')
