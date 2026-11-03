"""Source-bound draft severance adjustment journals.

Revision ID: p13sev261103c
Revises: p13sev261103b
"""

from alembic import op
import sqlalchemy as sa

revision = "p13sev261103c"
down_revision = "p13sev261103b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_sev_proposal_company_id",
        "payroll_severance_financial_proposals",
        ["company_id", "id"],
    )

    op.create_table(
        "payroll_severance_adjustment_journals",
        sa.Column(
            "id", sa.Integer(), sa.Identity(),
            primary_key=True,
        ),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column(
            "financial_proposal_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "journal_entry_id", sa.Integer(), nullable=False,
        ),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),

        sa.ForeignKeyConstraint(
            ["company_id", "financial_proposal_id"],
            [
                "payroll_severance_financial_proposals.company_id",
                "payroll_severance_financial_proposals.id",
            ],
            name="fk_sev_adjustment_proposal",
            ondelete="RESTRICT",
        ),

        sa.ForeignKeyConstraint(
            ["company_id", "journal_entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_sev_adjustment_journal",
            ondelete="RESTRICT",
        ),

        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_sev_adjustment_creator",
            ondelete="RESTRICT",
        ),

        sa.UniqueConstraint(
            "company_id",
            "financial_proposal_id",
            name="uq_sev_adjustment_proposal",
        ),

        sa.UniqueConstraint(
            "company_id",
            "journal_entry_id",
            name="uq_sev_adjustment_journal",
        ),
    )

    op.execute("""
        CREATE FUNCTION protect_severance_adjustment_journal()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.status::text = 'posted'
               AND EXISTS (
                   SELECT 1
                   FROM payroll_severance_adjustment_journals s
                   WHERE s.company_id = NEW.company_id
                     AND s.journal_entry_id = NEW.id
               )
            THEN
                RAISE EXCEPTION
                    'Severance adjustment requires verified approval';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)

    op.execute("""
        CREATE TRIGGER trg_sev_adjustment_block_post
        BEFORE UPDATE OF status ON journal_entries
        FOR EACH ROW
        EXECUTE FUNCTION protect_severance_adjustment_journal();
    """)

    op.execute("""
        CREATE FUNCTION protect_severance_adjustment_link()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            journal_state text;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION
                    'Severance adjustment link is immutable';
            END IF;

            SELECT j.status::text
            INTO journal_state
            FROM journal_entries j
            WHERE j.id = NEW.journal_entry_id
              AND j.company_id = NEW.company_id
            FOR UPDATE;

            IF journal_state IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION
                    'Severance adjustment requires a draft journal';
            END IF;

            RETURN NEW;
        END;
        $$;
    """)

    op.execute("""
        CREATE TRIGGER trg_sev_adjustment_link_guard
        BEFORE INSERT OR UPDATE OR DELETE
        ON payroll_severance_adjustment_journals
        FOR EACH ROW
        EXECUTE FUNCTION protect_severance_adjustment_link();
    """)


def downgrade():
    op.execute("""
        DROP TRIGGER trg_sev_adjustment_link_guard
        ON payroll_severance_adjustment_journals
    """)
    op.execute(
        "DROP FUNCTION protect_severance_adjustment_link()"
    )

    op.execute("""
        DROP TRIGGER trg_sev_adjustment_block_post
        ON journal_entries
    """)
    op.execute(
        "DROP FUNCTION protect_severance_adjustment_journal()"
    )

    op.drop_table("payroll_severance_adjustment_journals")
    op.drop_constraint(
        "uq_sev_proposal_company_id",
        "payroll_severance_financial_proposals",
        type_="unique",
    )
