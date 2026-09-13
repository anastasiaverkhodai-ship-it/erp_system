"""immutable bank reconciliation reversals

Revision ID: e5b7c9d1a024
Revises: d4f6a8b2c913
"""

from alembic import op
import sqlalchemy as sa


revision = "e5b7c9d1a024"
down_revision = "d4f6a8b2c913"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing B4 rows are all ACTIVE originals because B5 did not
    # previously exist. Therefore each existing row can be migrated
    # directly into the immutable-event representation.

    op.drop_index(
        "uq_bank_statement_reconciliation_active_line",
        table_name="bank_statement_reconciliations",
    )
    op.drop_index(
        "ix_bank_statement_reconciliation_payment_active",
        table_name="bank_statement_reconciliations",
    )
    op.drop_index(
        "ix_bank_statement_reconciliations_status",
        table_name="bank_statement_reconciliations",
    )

    op.drop_constraint(
        "ck_bank_statement_reconciliations_reversal_state",
        "bank_statement_reconciliations",
        type_="check",
    )
    op.drop_constraint(
        "ck_bank_statement_reconciliations_status",
        "bank_statement_reconciliations",
        type_="check",
    )
    op.drop_constraint(
        "fk_bank_statement_reconciliations_reversed_by",
        "bank_statement_reconciliations",
        type_="foreignkey",
    )

    op.add_column(
        "bank_statement_reconciliations",
        sa.Column(
            "reversal_of_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_unique_constraint(
        "uq_bank_statement_reconciliations_reversal_of_id",
        "bank_statement_reconciliations",
        ["reversal_of_id"],
    )

    op.create_foreign_key(
        "fk_bank_statement_reconciliations_reversal_of",
        "bank_statement_reconciliations",
        "bank_statement_reconciliations",
        ["company_id", "reversal_of_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )

    op.create_check_constraint(
        "ck_bank_statement_reconciliations_not_self_reversal",
        "bank_statement_reconciliations",
        "reversal_of_id IS NULL OR reversal_of_id <> id",
    )

    op.create_index(
        "ix_bank_statement_reconciliations_reversal_of_id",
        "bank_statement_reconciliations",
        ["reversal_of_id"],
    )

    op.create_table(
        "bank_statement_reconciliation_active_links",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "company_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "bank_statement_line_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "payment_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "reconciliation_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=(
                "fk_bank_reconciliation_active_link_company"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "bank_statement_line_id",
            ],
            [
                "bank_statement_lines.company_id",
                "bank_statement_lines.id",
            ],
            name="fk_bank_reconciliation_active_link_line",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "payment_id",
            ],
            [
                "payments.company_id",
                "payments.id",
            ],
            name="fk_bank_reconciliation_active_link_payment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "reconciliation_id",
            ],
            [
                "bank_statement_reconciliations.company_id",
                "bank_statement_reconciliations.id",
            ],
            name="fk_bank_reconciliation_active_link_event",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id",
            "bank_statement_line_id",
            name="uq_bank_reconciliation_active_link_line",
        ),
        sa.UniqueConstraint(
            "company_id",
            "reconciliation_id",
            name="uq_bank_reconciliation_active_link_event",
        ),
    )

    op.create_index(
        "ix_bank_reconciliation_active_links_company_id",
        "bank_statement_reconciliation_active_links",
        ["company_id"],
    )
    op.create_index(
        "ix_bank_reconciliation_active_links_line_id",
        "bank_statement_reconciliation_active_links",
        ["bank_statement_line_id"],
    )
    op.create_index(
        "ix_bank_reconciliation_active_links_payment_id",
        "bank_statement_reconciliation_active_links",
        ["payment_id"],
    )
    op.create_index(
        "ix_bank_reconciliation_active_links_reconciliation_id",
        "bank_statement_reconciliation_active_links",
        ["reconciliation_id"],
    )

    # Materialize current state for any pre-existing B4 rows.
    op.execute(
        """
        INSERT INTO bank_statement_reconciliation_active_links
        (
            company_id,
            bank_statement_line_id,
            payment_id,
            reconciliation_id
        )
        SELECT
            company_id,
            bank_statement_line_id,
            payment_id,
            id
        FROM bank_statement_reconciliations
        WHERE status = 'active'
        """
    )

    op.drop_column(
        "bank_statement_reconciliations",
        "reversed_at",
    )
    op.drop_column(
        "bank_statement_reconciliations",
        "reversed_by",
    )
    op.drop_column(
        "bank_statement_reconciliations",
        "status",
    )


def downgrade() -> None:
    connection = op.get_bind()

    reversal_count = connection.execute(
        sa.text(
            """
            SELECT count(*)
            FROM bank_statement_reconciliations
            WHERE reversal_of_id IS NOT NULL
            """
        )
    ).scalar_one()

    if reversal_count:
        raise RuntimeError(
            "Cannot downgrade immutable bank reconciliation "
            "history after reversal events exist. "
            "The old mutable-status schema cannot faithfully "
            "represent reversal + re-entry history."
        )

    op.add_column(
        "bank_statement_reconciliations",
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=True,
        ),
    )
    op.add_column(
        "bank_statement_reconciliations",
        sa.Column(
            "reversed_by",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "bank_statement_reconciliations",
        sa.Column(
            "reversed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.execute(
        """
        UPDATE bank_statement_reconciliations
        SET status = 'active'
        """
    )

    op.alter_column(
        "bank_statement_reconciliations",
        "status",
        nullable=False,
    )

    op.create_foreign_key(
        "fk_bank_statement_reconciliations_reversed_by",
        "bank_statement_reconciliations",
        "users",
        ["reversed_by"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_check_constraint(
        "ck_bank_statement_reconciliations_status",
        "bank_statement_reconciliations",
        "status IN ('active', 'reversed')",
    )

    op.create_check_constraint(
        "ck_bank_statement_reconciliations_reversal_state",
        "bank_statement_reconciliations",
        (
            "(status = 'active' "
            "AND reversed_by IS NULL "
            "AND reversed_at IS NULL) "
            "OR "
            "(status = 'reversed' "
            "AND reversed_by IS NOT NULL "
            "AND reversed_at IS NOT NULL)"
        ),
    )

    op.drop_table(
        "bank_statement_reconciliation_active_links"
    )

    op.drop_index(
        "ix_bank_statement_reconciliations_reversal_of_id",
        table_name="bank_statement_reconciliations",
    )

    op.drop_constraint(
        "ck_bank_statement_reconciliations_not_self_reversal",
        "bank_statement_reconciliations",
        type_="check",
    )

    op.drop_constraint(
        "fk_bank_statement_reconciliations_reversal_of",
        "bank_statement_reconciliations",
        type_="foreignkey",
    )

    op.drop_constraint(
        "uq_bank_statement_reconciliations_reversal_of_id",
        "bank_statement_reconciliations",
        type_="unique",
    )

    op.drop_column(
        "bank_statement_reconciliations",
        "reversal_of_id",
    )

    op.create_index(
        "ix_bank_statement_reconciliations_status",
        "bank_statement_reconciliations",
        ["status"],
    )

    op.create_index(
        "uq_bank_statement_reconciliation_active_line",
        "bank_statement_reconciliations",
        [
            "company_id",
            "bank_statement_line_id",
        ],
        unique=True,
        postgresql_where=sa.text(
            "status = 'active'"
        ),
    )

    op.create_index(
        "ix_bank_statement_reconciliation_payment_active",
        "bank_statement_reconciliations",
        [
            "company_id",
            "payment_id",
            "id",
        ],
        postgresql_where=sa.text(
            "status = 'active'"
        ),
    )
