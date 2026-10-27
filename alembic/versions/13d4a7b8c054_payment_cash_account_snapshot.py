"""Historical cash account snapshot for ordinary Payment.

Revision ID: 13d4a7b8c054
Revises: 13d4a7b8c053

Legacy cash payments remain NULL. Never infer physical
historical funds from current CashDesk account mapping.
"""

from alembic import op
import sqlalchemy as sa

revision = "13d4a7b8c054"
down_revision = "13d4a7b8c053"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "payments",
        sa.Column(
            "cash_account_id_snapshot",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_payment_cash_account_snapshot",
        "payments",
        "accounts",
        ["cash_account_id_snapshot"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_check_constraint(
        "ck_payment_cash_snapshot_source",
        "payments",
        "("
        "cash_desk_id IS NOT NULL "
        "OR cash_account_id_snapshot IS NULL"
        ") AND ("
        "bank_account_id IS NULL "
        "OR cash_account_id_snapshot IS NULL"
        ")",
    )

    op.execute("""
        CREATE FUNCTION enforce_payment_cash_snapshot_immutable()
        RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.cash_account_id_snapshot IS NOT NULL
               AND NEW.cash_account_id_snapshot
                   IS DISTINCT FROM OLD.cash_account_id_snapshot
            THEN
                RAISE EXCEPTION
                    'Payment historical cash account is immutable';
            END IF;

            IF OLD.status IN ('confirmed', 'cancelled')
               AND NEW.cash_account_id_snapshot
                   IS DISTINCT FROM OLD.cash_account_id_snapshot
            THEN
                RAISE EXCEPTION
                    'Cannot change cash snapshot of finalized Payment';
            END IF;

            RETURN NEW;
        END;
        $$;
    """)

    op.execute("""
        CREATE TRIGGER trg_payment_cash_snapshot_immutable
        BEFORE UPDATE ON payments
        FOR EACH ROW
        EXECUTE FUNCTION enforce_payment_cash_snapshot_immutable();
    """)


def downgrade():
    existing = op.get_bind().execute(
        sa.text("""
            SELECT count(*)
            FROM payments
            WHERE cash_account_id_snapshot IS NOT NULL
        """)
    ).scalar_one()

    if existing:
        raise RuntimeError(
            "Cannot downgrade c054: historical Payment snapshots exist"
        )

    op.execute(
        "DROP TRIGGER trg_payment_cash_snapshot_immutable ON payments"
    )
    op.execute(
        "DROP FUNCTION enforce_payment_cash_snapshot_immutable()"
    )

    op.drop_constraint(
        "ck_payment_cash_snapshot_source",
        "payments",
        type_="check",
    )
    op.drop_constraint(
        "fk_payment_cash_account_snapshot",
        "payments",
        type_="foreignkey",
    )
    op.drop_column("payments", "cash_account_id_snapshot")
