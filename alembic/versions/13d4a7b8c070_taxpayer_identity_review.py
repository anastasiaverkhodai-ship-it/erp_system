"""Independent reviewed taxpayer identity for regulatory reporting."""

from alembic import op
import sqlalchemy as sa

revision = "13d4a7b8c070"
down_revision = "13d4a7b8c069"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payroll_taxpayer_identity_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("identity_kind", sa.String(20), nullable=False),
        sa.Column("tax_number", sa.String(50), nullable=False),
        sa.Column("evidence_reference", sa.String(150), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column(
            "review_status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column(
            "reviewed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "employee_id"],
            ["employees.company_id", "employees.id"],
            name="fk_taxpayer_identity_review_employee",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_taxpayer_identity_review_creator",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
            name="fk_taxpayer_identity_review_reviewer",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_taxpayer_identity_review_company_id",
        ),
        sa.CheckConstraint(
            "identity_kind = 'rnokpp'",
            name="ck_taxpayer_identity_kind",
        ),
        sa.CheckConstraint(
            "tax_number ~ '^[0-9]{10}$'",
            name="ck_taxpayer_identity_number",
        ),
        sa.CheckConstraint(
            "length(trim(evidence_reference)) > 0",
            name="ck_taxpayer_identity_reference",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_taxpayer_identity_validity",
        ),
        sa.CheckConstraint(
            "review_status IN ('pending','verified','rejected')",
            name="ck_taxpayer_identity_status",
        ),
        sa.CheckConstraint(
            "(review_status = 'pending' AND "
            "reviewed_by IS NULL AND reviewed_at IS NULL) OR "
            "(review_status IN ('verified','rejected') AND "
            "reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL "
            "AND reviewed_by <> created_by)",
            name="ck_taxpayer_identity_independent_review",
        ),
    )

    op.create_index(
        "ix_taxpayer_identity_review_employee_dates",
        "payroll_taxpayer_identity_reviews",
        ["company_id", "employee_id", "valid_from"],
    )

    # A completed review cannot be edited or deleted.
    # Corrections require a new reviewed evidence record.
    op.execute("""
        CREATE FUNCTION erp_taxpayer_review_immutable()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION
                    'Taxpayer identity review cannot be deleted';
            END IF;

            IF OLD.review_status <> 'pending' THEN
                RAISE EXCEPTION
                    'Completed taxpayer identity review is immutable';
            END IF;

            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.company_id IS DISTINCT FROM OLD.company_id
               OR NEW.employee_id IS DISTINCT FROM OLD.employee_id
               OR NEW.identity_kind IS DISTINCT FROM OLD.identity_kind
               OR NEW.tax_number IS DISTINCT FROM OLD.tax_number
               OR NEW.evidence_reference
                    IS DISTINCT FROM OLD.evidence_reference
               OR NEW.valid_from IS DISTINCT FROM OLD.valid_from
               OR NEW.valid_to IS DISTINCT FROM OLD.valid_to
               OR NEW.created_by IS DISTINCT FROM OLD.created_by
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
               OR NEW.review_status = 'pending'
            THEN
                RAISE EXCEPTION
                    'Only a final independent review decision is permitted';
            END IF;

            RETURN NEW;
        END;
        $$;
    """)

    op.execute("""
        CREATE TRIGGER trg_taxpayer_review_immutable
        BEFORE UPDATE OR DELETE
        ON payroll_taxpayer_identity_reviews
        FOR EACH ROW
        EXECUTE FUNCTION erp_taxpayer_review_immutable();
    """)


def downgrade():
    op.execute(
        "DROP TRIGGER IF EXISTS trg_taxpayer_review_immutable "
        "ON payroll_taxpayer_identity_reviews"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS erp_taxpayer_review_immutable()"
    )
    op.drop_index(
        "ix_taxpayer_identity_review_employee_dates",
        table_name="payroll_taxpayer_identity_reviews",
    )
    op.drop_table("payroll_taxpayer_identity_reviews")
