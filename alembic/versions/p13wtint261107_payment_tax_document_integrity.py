"""Immutable document-hash verification and independent review history.

Revision ID: p13wtint261107
Revises: p13wtevid261107
"""

from alembic import op
import sqlalchemy as sa

revision = "p13wtint261107"
down_revision = "p13wtevid261107"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payroll_payment_tax_document_integrity",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("company_id", sa.Integer, nullable=False),
        sa.Column("evidence_id", sa.Integer, nullable=False),
        sa.Column("document_sha256", sa.String(64), nullable=False),
        sa.Column("document_size_bytes", sa.Integer, nullable=False),
        sa.Column("request_key", sa.String(150), nullable=False),
        sa.Column("checked_by", sa.Integer, nullable=False),
        sa.Column(
            "check_status", sa.String(40), nullable=False,
            server_default="hash_matched_only",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "evidence_id"],
            ["payroll_payment_tax_evidence.company_id",
             "payroll_payment_tax_evidence.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["checked_by"], ["users.id"], ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id", "id", name="uq_pptdi_company_id",
        ),
        sa.UniqueConstraint(
            "company_id", "evidence_id",
            name="uq_pptdi_evidence",
        ),
        sa.UniqueConstraint(
            "company_id", "request_key",
            name="uq_pptdi_request",
        ),
        sa.CheckConstraint(
            "document_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_pptdi_sha",
        ),
        sa.CheckConstraint(
            "document_size_bytes BETWEEN 1 AND 5242880",
            name="ck_pptdi_size",
        ),
        sa.CheckConstraint(
            "check_status = 'hash_matched_only'",
            name="ck_pptdi_no_statutory_approval",
        ),
    )

    op.create_table(
        "payroll_payment_tax_document_reviews",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("company_id", sa.Integer, nullable=False),
        sa.Column("integrity_check_id", sa.Integer, nullable=False),
        sa.Column("evidence_id", sa.Integer, nullable=False),
        sa.Column("reviewed_by", sa.Integer, nullable=False),
        sa.Column("review_reference", sa.String(500), nullable=False),
        sa.Column("request_key", sa.String(150), nullable=False),
        sa.Column(
            "review_status", sa.String(60),
            nullable=False,
            server_default="internal_review_only",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "integrity_check_id"],
            ["payroll_payment_tax_document_integrity.company_id",
             "payroll_payment_tax_document_integrity.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "evidence_id"],
            ["payroll_payment_tax_evidence.company_id",
             "payroll_payment_tax_evidence.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"], ["users.id"], ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id", "id", name="uq_pptdr_company_id",
        ),
        sa.UniqueConstraint(
            "company_id", "integrity_check_id",
            name="uq_pptdr_check",
        ),
        sa.UniqueConstraint(
            "company_id", "request_key",
            name="uq_pptdr_request",
        ),
        sa.CheckConstraint(
            "review_status = 'internal_review_only'",
            name="ck_pptdr_no_statutory_approval",
        ),
        sa.CheckConstraint(
            "length(btrim(review_reference)) > 0",
            name="ck_pptdr_reference",
        ),
    )

    op.execute("""
        CREATE FUNCTION pptdi_reject_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION
                'Payment-tax document reviews are append-only';
        END;
        $$
    """)

    for table, trigger in (
        ("payroll_payment_tax_document_integrity",
         "trg_pptdi_immutable"),
        ("payroll_payment_tax_document_reviews",
         "trg_pptdr_immutable"),
    ):
        op.execute(f"""
            CREATE TRIGGER {trigger}
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW
            EXECUTE FUNCTION pptdi_reject_mutation()
        """)

    op.execute("""
        CREATE FUNCTION pptdr_validate_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            evidence_author integer;
            candidate_author integer;
            integrity_author integer;
            actual_evidence integer;
        BEGIN
            SELECT i.evidence_id, i.checked_by,
                   e.created_by, c.created_by
            INTO actual_evidence, integrity_author,
                 evidence_author, candidate_author
            FROM payroll_payment_tax_document_integrity i
            JOIN payroll_payment_tax_evidence e
              ON e.id=i.evidence_id
             AND e.company_id=i.company_id
            JOIN payroll_payment_tax_allocation_candidates c
              ON c.id=e.candidate_id
             AND c.company_id=e.company_id
            WHERE i.company_id=NEW.company_id
              AND i.id=NEW.integrity_check_id;

            IF actual_evidence IS NULL
               OR actual_evidence <> NEW.evidence_id
               OR NEW.reviewed_by IN (
                   evidence_author,
                   candidate_author,
                   integrity_author
               )
            THEN
                RAISE EXCEPTION
                    'Independent review identity violation';
            END IF;

            RETURN NEW;
        END;
        $$
    """)

    op.execute("""
        CREATE TRIGGER trg_pptdr_validate
        BEFORE INSERT
        ON payroll_payment_tax_document_reviews
        FOR EACH ROW
        EXECUTE FUNCTION pptdr_validate_insert()
    """)


def downgrade():
    op.execute("""
        DROP TRIGGER trg_pptdr_validate
        ON payroll_payment_tax_document_reviews
    """)
    op.execute("DROP FUNCTION pptdr_validate_insert()")

    for table, trigger in (
        ("payroll_payment_tax_document_integrity",
         "trg_pptdi_immutable"),
        ("payroll_payment_tax_document_reviews",
         "trg_pptdr_immutable"),
    ):
        op.execute(f"DROP TRIGGER {trigger} ON {table}")

    op.drop_table("payroll_payment_tax_document_reviews")
    op.drop_table("payroll_payment_tax_document_integrity")
    op.execute("DROP FUNCTION pptdi_reject_mutation()")
