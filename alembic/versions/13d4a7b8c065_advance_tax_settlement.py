"""Support USC advance remittances and preserve their settlement evidence."""
from alembic import op

revision = '13d4a7b8c065'
down_revision = '13d4a7b8c064'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint('ck_advance_tax_remittance_component', 'payroll_advance_tax_remittances', type_='check')
    op.create_check_constraint('ck_advance_tax_remittance_component', 'payroll_advance_tax_remittances',
        "component IN ('personal_income_tax', 'military_levy', 'unified_social_contribution')")
    op.execute('''CREATE FUNCTION preserve_advance_tax_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'Advance tax evidence is immutable; use a dated journal reversal'; END $$''')
    op.execute('''CREATE TRIGGER advance_tax_evidence_immutable BEFORE UPDATE OR DELETE
        ON payroll_advance_tax_remittances FOR EACH ROW EXECUTE FUNCTION preserve_advance_tax_evidence()''')
    op.execute('''CREATE TRIGGER advance_tax_evidence_no_truncate BEFORE TRUNCATE
        ON payroll_advance_tax_remittances FOR EACH STATEMENT EXECUTE FUNCTION preserve_advance_tax_evidence()''')


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM payroll_advance_tax_remittances)
        THEN RAISE EXCEPTION 'Cannot downgrade with advance tax settlement history'; END IF; END $$""")
    op.execute('DROP TRIGGER advance_tax_evidence_immutable ON payroll_advance_tax_remittances')
    op.execute('DROP TRIGGER advance_tax_evidence_no_truncate ON payroll_advance_tax_remittances')
    op.execute('DROP FUNCTION preserve_advance_tax_evidence()')
    op.drop_constraint('ck_advance_tax_remittance_component', 'payroll_advance_tax_remittances', type_='check')
    op.create_check_constraint('ck_advance_tax_remittance_component', 'payroll_advance_tax_remittances',
        "component IN ('personal_income_tax', 'military_levy')")
