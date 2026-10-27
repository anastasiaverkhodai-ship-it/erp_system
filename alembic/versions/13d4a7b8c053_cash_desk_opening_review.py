"""Independent review evidence for physical cash openings.

Revision ID: 13d4a7b8c053
Revises: 13d4a7b8c052

A review records an explicit human attestation, not automatic
verification of an external document or GL-derived cash quantity.
"""

from alembic import op
import sqlalchemy as sa

revision = "13d4a7b8c053"
down_revision = "13d4a7b8c052"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_cash_desk_open_company_id",
        "cash_desk_opening_evidence",
        ["company_id", "id"],
    )

    op.create_table(
        "cash_desk_opening_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("opening_evidence_id", sa.Integer(), nullable=False),
        sa.Column("reviewed_by", sa.Integer(), nullable=False),
        sa.Column("review_reference", sa.String(255), nullable=False),
        sa.Column(
            "reviewed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_cash_open_review_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "opening_evidence_id"],
            [
                "cash_desk_opening_evidence.company_id",
                "cash_desk_opening_evidence.id",
            ],
            name="fk_cash_open_review_evidence",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
            name="fk_cash_open_review_actor",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "opening_evidence_id",
            name="uq_cash_open_review_once",
        ),
        sa.CheckConstraint(
            "length(trim(review_reference)) > 0",
            name="ck_cash_open_review_reference",
        ),
    )

    # Prevent self-review even if clients bypass application services.
    op.execute("""
        CREATE FUNCTION validate_cash_opening_review()
        RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            source_actor integer;
            source_currency varchar(3);
            desk_currency varchar(3);
        BEGIN
            SELECT e.attested_by, e.currency_code, d.currency_code
            INTO source_actor, source_currency, desk_currency
            FROM cash_desk_opening_evidence e
            JOIN cash_desks d
              ON d.company_id = e.company_id
             AND d.id = e.cash_desk_id
            WHERE e.company_id = NEW.company_id
              AND e.id = NEW.opening_evidence_id
            FOR SHARE OF e, d;

            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'Cash opening evidence or desk missing';
            END IF;

            IF source_actor = NEW.reviewed_by THEN
                RAISE EXCEPTION
                    'Cash opening reviewer must be independent';
            END IF;

            IF source_currency <> desk_currency THEN
                RAISE EXCEPTION
                    'Cash opening currency differs from cash desk';
            END IF;

            RETURN NEW;
        END;
        $$;
    """)

    op.execute("""
        CREATE TRIGGER trg_validate_cash_opening_review
        BEFORE INSERT ON cash_desk_opening_reviews
        FOR EACH ROW
        EXECUTE FUNCTION validate_cash_opening_review();
    """)

    op.execute("""
        CREATE FUNCTION cash_opening_review_readonly()
        RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION
                'Cash opening review history is immutable';
        END;
        $$;
    """)

    op.execute("""
        CREATE TRIGGER trg_cash_opening_review_readonly
        BEFORE UPDATE OR DELETE ON cash_desk_opening_reviews
        FOR EACH ROW
        EXECUTE FUNCTION cash_opening_review_readonly();
    """)


def downgrade():
    count = op.get_bind().execute(
        sa.text("SELECT count(*) FROM cash_desk_opening_reviews")
    ).scalar_one()

    if count:
        raise RuntimeError(
            "Cannot downgrade c053 while cash opening reviews exist"
        )

    op.execute(
        "DROP TRIGGER trg_cash_opening_review_readonly "
        "ON cash_desk_opening_reviews"
    )
    op.execute("DROP FUNCTION cash_opening_review_readonly()")

    op.execute(
        "DROP TRIGGER trg_validate_cash_opening_review "
        "ON cash_desk_opening_reviews"
    )
    op.execute("DROP FUNCTION validate_cash_opening_review()")

    op.drop_table("cash_desk_opening_reviews")
    op.drop_constraint(
        "uq_cash_desk_open_company_id",
        "cash_desk_opening_evidence",
        type_="unique",
    )
