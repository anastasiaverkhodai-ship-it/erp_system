"""Immutable payroll cash accounting account snapshot.

Revision ID: 13d4a7b8c050
Revises: 13d4a7b8c049

No automatic restoration of historic cash source provenance.
"""

from alembic import op
import sqlalchemy as sa

revision = "13d4a7b8c050"
down_revision = "13d4a7b8c049"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()

    existing_cash = connection.execute(
        sa.text(
            "SELECT count(*) FROM payroll_disbursements "
            "WHERE cash_desk_id IS NOT NULL"
        )
    ).scalar_one()

    if existing_cash:
        raise RuntimeError(
            "Cannot upgrade c050: existing cash payroll "
            "requires independently verified account provenance"
        )

    op.add_column(
        "payroll_disbursements",
        sa.Column(
            "cash_account_id_snapshot",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_payroll_disbursements_cash_account_snapshot",
        "payroll_disbursements",
        "accounts",
        ["company_id", "cash_account_id_snapshot"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )

    op.create_check_constraint(
        "ck_payroll_disbursement_cash_account_snapshot",
        "payroll_disbursements",
        "(cash_desk_id IS NULL AND "
        "cash_account_id_snapshot IS NULL) OR "
        "(cash_desk_id IS NOT NULL AND "
        "cash_account_id_snapshot IS NOT NULL)",
    )

    op.execute("""
        CREATE FUNCTION payroll_cash_snapshot_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.cash_account_id_snapshot
               IS DISTINCT FROM OLD.cash_account_id_snapshot
            THEN
                RAISE EXCEPTION
                    'Historical payroll cash account is immutable';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)

    op.execute("""
        CREATE TRIGGER trg_payroll_cash_snapshot_immutable
        BEFORE UPDATE ON payroll_disbursements
        FOR EACH ROW
        EXECUTE FUNCTION payroll_cash_snapshot_immutable();
    """)


def downgrade():
    connection = op.get_bind()

    cash_count = connection.execute(
        sa.text(
            "SELECT count(*) FROM payroll_disbursements "
            "WHERE cash_desk_id IS NOT NULL"
        )
    ).scalar_one()

    if cash_count:
        raise RuntimeError(
            "Cannot downgrade c050 with existing cash payroll: "
            "historical provenance must be preserved"
        )

    op.execute(
        "DROP TRIGGER trg_payroll_cash_snapshot_immutable "
        "ON payroll_disbursements"
    )
    op.execute(
        "DROP FUNCTION payroll_cash_snapshot_immutable()"
    )

    op.drop_constraint(
        "ck_payroll_disbursement_cash_account_snapshot",
        "payroll_disbursements",
        type_="check",
    )
    op.drop_constraint(
        "fk_payroll_disbursements_cash_account_snapshot",
        "payroll_disbursements",
        type_="foreignkey",
    )
    op.drop_column(
        "payroll_disbursements",
        "cash_account_id_snapshot",
    )
