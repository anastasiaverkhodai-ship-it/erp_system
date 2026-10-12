"""Add payroll deduction legal-rule approval records.

Revision ID: 13d4a7b8c022
Revises: 13d4a7b8c021
"""

from alembic import op
import sqlalchemy as sa


revision = "13d4a7b8c022"
down_revision = "13d4a7b8c021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payroll_deduction_rule_approvals",
        sa.Column(
            "id",
            sa.Integer(),
            sa.Identity(),
            primary_key=True,
        ),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("legal_category", sa.String(50), nullable=False),
        sa.Column("rule_version", sa.String(100), nullable=False),
        sa.Column("legal_reference", sa.String(500), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "approved_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "revoked_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_pdra_company_id_id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "legal_category",
            "rule_version",
            name="uq_pdra_company_category_version",
        ),
        sa.CheckConstraint(
            "legal_category IN ("
            "'employer_statutory', "
            "'enforcement_other', "
            "'enforcement_special', "
            "'minor_child_alimony')",
            name="ck_pdra_category",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'revoked')",
            name="ck_pdra_status",
        ),
        sa.CheckConstraint(
            "char_length(trim(rule_version)) > 0",
            name="ck_pdra_version",
        ),
        sa.CheckConstraint(
            "char_length(trim(legal_reference)) > 0",
            name="ck_pdra_reference",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_pdra_dates",
        ),
        sa.CheckConstraint(
            "(status = 'pending' "
            "AND approved_by IS NULL AND approved_at IS NULL "
            "AND revoked_by IS NULL AND revoked_at IS NULL) "
            "OR (status = 'approved' "
            "AND approved_by IS NOT NULL AND approved_at IS NOT NULL "
            "AND revoked_by IS NULL AND revoked_at IS NULL) "
            "OR (status = 'revoked' "
            "AND approved_by IS NOT NULL AND approved_at IS NOT NULL "
            "AND revoked_by IS NOT NULL AND revoked_at IS NOT NULL)",
            name="ck_pdra_lifecycle",
        ),
        sa.CheckConstraint(
            "approved_by IS NULL OR approved_by <> created_by",
            name="ck_pdra_four_eyes",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= approved_at",
            name="ck_pdra_revocation_order",
        ),
    )


    op.create_table(
        "payroll_deduction_rule_approval_events",
        sa.Column(
            "id",
            sa.Integer(),
            sa.Identity(),
            primary_key=True,
        ),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("approval_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column(
            "actor_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "approval_id"],
            [
                "payroll_deduction_rule_approvals.company_id",
                "payroll_deduction_rule_approvals.id",
            ],
            name="fk_pdrae_company_approval",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "event_type IN ('created', 'approved', 'revoked')",
            name="ck_pdrae_event_type",
        ),
    )

    op.execute(
        """
        CREATE FUNCTION pdrae_prevent_history_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'Payroll deduction approval history is append-only';
        END;
        $$
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_pdrae_no_update_delete
        BEFORE UPDATE OR DELETE
        ON payroll_deduction_rule_approval_events
        FOR EACH ROW
        EXECUTE FUNCTION pdrae_prevent_history_mutation()
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_pdrae_no_truncate
        BEFORE TRUNCATE
        ON payroll_deduction_rule_approval_events
        FOR EACH STATEMENT
        EXECUTE FUNCTION pdrae_prevent_history_mutation()
        """
    )


    op.execute(
        """
        CREATE FUNCTION pdra_record_lifecycle_event()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            action_name text;
            action_actor integer;
            action_time timestamptz;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'pending'
                   OR NEW.approved_by IS NOT NULL
                   OR NEW.revoked_by IS NOT NULL THEN
                    RAISE EXCEPTION
                        'New approval must be pending';
                END IF;

                action_name := 'created';
                action_actor := NEW.created_by;
                action_time := NEW.created_at;

            ELSIF TG_OP = 'UPDATE' THEN
                IF ROW(
                    NEW.id,
                    NEW.company_id,
                    NEW.legal_category,
                    NEW.rule_version,
                    NEW.legal_reference,
                    NEW.valid_from,
                    NEW.valid_to,
                    NEW.created_by,
                    NEW.created_at
                ) IS DISTINCT FROM ROW(
                    OLD.id,
                    OLD.company_id,
                    OLD.legal_category,
                    OLD.rule_version,
                    OLD.legal_reference,
                    OLD.valid_from,
                    OLD.valid_to,
                    OLD.created_by,
                    OLD.created_at
                ) THEN
                    RAISE EXCEPTION
                        'Approval identity and legal basis are immutable';
                END IF;

                IF OLD.status = 'pending'
                   AND NEW.status = 'approved'
                   AND NEW.approved_by IS NOT NULL
                   AND NEW.approved_by <> NEW.created_by
                   AND NEW.approved_at IS NOT NULL
                   AND NEW.revoked_by IS NULL
                   AND NEW.revoked_at IS NULL THEN

                    action_name := 'approved';
                    action_actor := NEW.approved_by;
                    action_time := NEW.approved_at;

                ELSIF OLD.status = 'approved'
                   AND NEW.status = 'revoked'
                   AND NEW.approved_by = OLD.approved_by
                   AND NEW.approved_at = OLD.approved_at
                   AND NEW.revoked_by IS NOT NULL
                   AND NEW.revoked_at IS NOT NULL
                   AND NEW.revoked_at >= NEW.approved_at THEN

                    action_name := 'revoked';
                    action_actor := NEW.revoked_by;
                    action_time := NEW.revoked_at;

                ELSE
                    RAISE EXCEPTION
                        'Invalid or duplicate approval transition';
                END IF;

            ELSE
                RAISE EXCEPTION
                    'Unsupported approval lifecycle operation';
            END IF;

            INSERT INTO payroll_deduction_rule_approval_events (
                company_id,
                approval_id,
                event_type,
                actor_id,
                occurred_at
            )
            VALUES (
                NEW.company_id,
                NEW.id,
                action_name,
                action_actor,
                action_time
            );

            RETURN NULL;
        END;
        $$
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_pdra_lifecycle_insert
        AFTER INSERT
        ON payroll_deduction_rule_approvals
        FOR EACH ROW
        EXECUTE FUNCTION pdra_record_lifecycle_event()
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_pdra_lifecycle_update
        AFTER UPDATE
        ON payroll_deduction_rule_approvals
        FOR EACH ROW
        EXECUTE FUNCTION pdra_record_lifecycle_event()
        """
    )

    op.execute(
        """
        CREATE FUNCTION pdra_reject_delete()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'Approval records cannot be deleted';
        END;
        $$
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_pdra_no_delete
        BEFORE DELETE
        ON payroll_deduction_rule_approvals
        FOR EACH ROW
        EXECUTE FUNCTION pdra_reject_delete()
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_pdra_no_truncate
        BEFORE TRUNCATE
        ON payroll_deduction_rule_approvals
        FOR EACH STATEMENT
        EXECUTE FUNCTION pdra_reject_delete()
        """
    )



def downgrade() -> None:
    op.execute(
        "LOCK TABLE payroll_deduction_rule_approvals "
        "IN ACCESS EXCLUSIVE MODE"
    )

    op.execute(
        "LOCK TABLE payroll_deduction_rule_approval_events "
        "IN ACCESS EXCLUSIVE MODE"
    )

    has_rows = op.get_bind().execute(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM payroll_deduction_rule_approvals"
            ") OR EXISTS ("
            "SELECT 1 FROM payroll_deduction_rule_approval_events"
            ")"
        )
    ).scalar_one()

    if has_rows:
        raise RuntimeError(
            "Cannot downgrade payroll deduction rule approvals "
            "with historical records"
        )

    op.drop_table("payroll_deduction_rule_approval_events")
    op.drop_table("payroll_deduction_rule_approvals")
    op.execute("DROP FUNCTION pdrae_prevent_history_mutation()")
    op.execute("DROP FUNCTION pdra_record_lifecycle_event()")
    op.execute("DROP FUNCTION pdra_reject_delete()")
