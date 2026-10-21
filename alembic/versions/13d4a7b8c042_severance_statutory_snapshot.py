"""Immutable payroll severance statutory snapshots.

Revision ID: 13d4a7b8c042
Revises: 13d4a7b8c041
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "13d4a7b8c042"
down_revision = "13d4a7b8c041"
branch_labels = None
depends_on = None


TABLE = "payroll_severance_statutory_snapshots"


def upgrade():
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column(
            "payroll_calculation_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("income_date", sa.Date(), nullable=False),
        sa.Column(
            "currency_code", sa.String(3), nullable=False
        ),
        sa.Column(
            "ordinary_gross", sa.Numeric(18, 2), nullable=False
        ),
        sa.Column(
            "severance_gross", sa.Numeric(18, 2), nullable=False
        ),
        sa.Column(
            "total_gross", sa.Numeric(18, 2), nullable=False
        ),
        sa.Column(
            "severance_source_ids", JSONB(), nullable=False
        ),
        sa.Column(
            "policy_code", sa.String(150), nullable=False
        ),
        sa.Column(
            "policy_valid_from", sa.Date(), nullable=False
        ),
        sa.Column(
            "policy_valid_to", sa.Date(), nullable=True
        ),
        sa.Column(
            "source_reference", sa.String(1000), nullable=False
        ),
        sa.Column(
            "income_code_4df", sa.String(3), nullable=False
        ),
        sa.Column(
            "personal_income_tax_base",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "military_levy_base",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "unified_social_contribution_base",
            sa.Numeric(18, 2),
            nullable=False,
        ),
        sa.Column(
            "fingerprint_sha256", sa.String(64), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "payroll_calculation_id"],
            [
                "payroll_calculations.company_id",
                "payroll_calculations.id",
            ],
            name="fk_psss_calculation",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id",
            "payroll_calculation_id",
            name="uq_psss_calculation",
        ),
        sa.UniqueConstraint(
            "company_id", "id",
            name="uq_psss_company_id",
        ),
        sa.CheckConstraint(
            "version = 1",
            name="ck_psss_version",
        ),
        sa.CheckConstraint(
            "currency_code = 'UAH'",
            name="ck_psss_currency",
        ),
        sa.CheckConstraint(
            "ordinary_gross >= 0 AND severance_gross > 0 "
            "AND total_gross = ordinary_gross + severance_gross",
            name="ck_psss_gross",
        ),
        sa.CheckConstraint(
            "personal_income_tax_base BETWEEN 0 AND total_gross "
            "AND military_levy_base BETWEEN 0 AND total_gross "
            "AND unified_social_contribution_base "
            "BETWEEN 0 AND total_gross",
            name="ck_psss_bases",
        ),
        sa.CheckConstraint(
            "income_code_4df = '127' "
            "AND length(trim(policy_code)) > 0 "
            "AND length(trim(source_reference)) > 0",
            name="ck_psss_policy",
        ),
        sa.CheckConstraint(
            "policy_valid_from <= income_date "
            "AND (policy_valid_to IS NULL "
            "OR income_date <= policy_valid_to)",
            name="ck_psss_dates",
        ),
        sa.CheckConstraint(
            "fingerprint_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_psss_fingerprint",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(severance_source_ids) = 'array' "
            "AND jsonb_array_length(severance_source_ids) > 0",
            name="ck_psss_sources",
        ),
    )

    # Database-level source and calculation verification.
    # One snapshot must exactly describe the immutable payroll lines.
    op.execute("""
CREATE FUNCTION payroll_severance_snapshot_validate()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    calc_gross numeric;
    source_count integer;
    matched_count integer;
    expected_count integer;
    severance_total numeric;
    ordinary_total numeric;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'Historical severance snapshot is immutable';
    END IF;

    SELECT gross_amount INTO calc_gross
    FROM payroll_calculations
    WHERE company_id = NEW.company_id
      AND id = NEW.payroll_calculation_id
    FOR SHARE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Severance payroll calculation missing';
    END IF;

    IF calc_gross <> NEW.total_gross THEN
        RAISE EXCEPTION 'Snapshot gross differs from calculation';
    END IF;

    IF jsonb_typeof(NEW.severance_source_ids) <> 'array'
       OR jsonb_array_length(NEW.severance_source_ids) = 0 THEN
        RAISE EXCEPTION 'Severance source array required';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(NEW.severance_source_ids) e(v)
        WHERE jsonb_typeof(e.v) <> 'number'
           OR e.v::text !~ '^[1-9][0-9]*$'
    ) THEN
        RAISE EXCEPTION 'Invalid severance source identifier';
    END IF;

    SELECT count(*), count(DISTINCT value)
      INTO source_count, matched_count
    FROM jsonb_array_elements_text(NEW.severance_source_ids) ids(value);

    IF source_count <> matched_count THEN
        RAISE EXCEPTION 'Duplicate severance source identifier';
    END IF;

    SELECT
        count(*) FILTER (WHERE line_type = 'severance'),
        COALESCE(sum(amount) FILTER (
            WHERE line_type = 'severance'), 0),
        COALESCE(sum(amount) FILTER (
            WHERE line_type <> 'severance'), 0)
    INTO expected_count, severance_total, ordinary_total
    FROM payroll_calculation_lines
    WHERE company_id = NEW.company_id
      AND payroll_calculation_id = NEW.payroll_calculation_id;

    IF expected_count <> source_count
       OR severance_total <> NEW.severance_gross
       OR ordinary_total <> NEW.ordinary_gross THEN
        RAISE EXCEPTION 'Snapshot line amounts or counts mismatch';
    END IF;

    SELECT count(*) INTO matched_count
    FROM payroll_calculation_lines l
    WHERE l.company_id = NEW.company_id
      AND l.payroll_calculation_id = NEW.payroll_calculation_id
      AND l.line_type = 'severance'
      AND l.currency_code = NEW.currency_code
      AND EXISTS (
          SELECT 1
          FROM jsonb_array_elements_text(
              NEW.severance_source_ids
          ) ids(value)
          WHERE ids.value::integer =
                l.source_termination_severance_id
      );

    IF matched_count <> source_count THEN
        RAISE EXCEPTION 'Severance source links mismatch';
    END IF;

    RETURN NEW;
END;
$$
""")

    op.execute("""
CREATE TRIGGER trg_psss_validate
BEFORE INSERT OR UPDATE OR DELETE
ON payroll_severance_statutory_snapshots
FOR EACH ROW
EXECUTE FUNCTION payroll_severance_snapshot_validate()
""")

    op.execute("""
CREATE FUNCTION payroll_severance_snapshot_no_truncate()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Historical severance snapshot cannot be truncated';
END;
$$
""")

    op.execute("""
CREATE TRIGGER trg_psss_no_truncate
BEFORE TRUNCATE
ON payroll_severance_statutory_snapshots
FOR EACH STATEMENT
EXECUTE FUNCTION payroll_severance_snapshot_no_truncate()
""")


def downgrade():
    # Historical snapshots must not be silently lost on downgrade.
    op.execute("""
DO $$
BEGIN
    LOCK TABLE payroll_severance_statutory_snapshots
        IN ACCESS EXCLUSIVE MODE;

    IF EXISTS (
        SELECT 1
        FROM payroll_severance_statutory_snapshots
    ) THEN
        RAISE EXCEPTION
            'Cannot discard historical severance statutory snapshots';
    END IF;
END;
$$
""")

    op.execute("""
DROP TRIGGER trg_psss_no_truncate
ON payroll_severance_statutory_snapshots
""")
    op.execute("""
DROP TRIGGER trg_psss_validate
ON payroll_severance_statutory_snapshots
""")

    op.drop_table(TABLE)

    op.execute("""
DROP FUNCTION payroll_severance_snapshot_no_truncate()
""")
    op.execute("""
DROP FUNCTION payroll_severance_snapshot_validate()
""")
