"""Encrypted immutable payroll tax evidence custody.

Revision ID: p13wtcust261107
Revises: p13wtint261107
"""

from alembic import op
import sqlalchemy as sa

revision = "p13wtcust261107"
down_revision = "p13wtint261107"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payroll_payment_tax_secure_custody",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("company_id", sa.Integer, nullable=False),
        sa.Column("evidence_id", sa.Integer, nullable=False),
        sa.Column("integrity_check_id", sa.Integer, nullable=False),
        sa.Column("key_id", sa.String(100), nullable=False),
        sa.Column("nonce", sa.LargeBinary, nullable=False),
        sa.Column("ciphertext", sa.LargeBinary, nullable=False),
        sa.Column("document_sha256", sa.String(64), nullable=False),
        sa.Column("document_size_bytes", sa.Integer, nullable=False),
        sa.Column("created_by", sa.Integer, nullable=False),
        sa.Column("request_key", sa.String(150), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "evidence_id"],
            [
                "payroll_payment_tax_evidence.company_id",
                "payroll_payment_tax_evidence.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "integrity_check_id"],
            [
                "payroll_payment_tax_document_integrity.company_id",
                "payroll_payment_tax_document_integrity.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id", "id",
            name="uq_pptsc_company_id",
        ),
        sa.UniqueConstraint(
            "company_id", "evidence_id",
            name="uq_pptsc_evidence",
        ),
        sa.UniqueConstraint(
            "company_id", "request_key",
            name="uq_pptsc_request",
        ),
        sa.UniqueConstraint(
            "key_id", "nonce",
            name="uq_pptsc_key_nonce",
        ),
        sa.CheckConstraint(
            "octet_length(nonce)=12",
            name="ck_pptsc_nonce",
        ),
        sa.CheckConstraint(
            "octet_length(ciphertext)>16",
            name="ck_pptsc_ciphertext",
        ),
        sa.CheckConstraint(
            "document_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_pptsc_sha",
        ),
        sa.CheckConstraint(
            "document_size_bytes BETWEEN 1 AND 5242880",
            name="ck_pptsc_size",
        ),
    )

    op.execute("""
        CREATE FUNCTION pptsc_authorized(
            actor integer,
            company integer,
            permission_name text
        )
        RETURNS boolean
        LANGUAGE sql STABLE AS $$
            SELECT EXISTS (
                SELECT 1
                FROM users u
                JOIN user_company_roles ur
                  ON ur.user_id=u.id
                JOIN role_permissions rp
                  ON rp.role_id=ur.role_id
                JOIN permissions p
                  ON p.id=rp.permission_id
                WHERE u.id=actor
                  AND u.is_active
                  AND ur.company_id=company
                  AND ur.is_active
                  AND p.name=permission_name
            )
        $$
    """)

    op.execute("""
        CREATE FUNCTION pptsc_guard()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            source_digest text;
            verified_digest text;
            verified_size integer;
            integrity_evidence integer;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'Custody is append-only';
            END IF;

            IF NOT pptsc_authorized(
                NEW.created_by,
                NEW.company_id,
                'employees.manage'
            ) THEN
                RAISE EXCEPTION 'Unauthorized custody author';
            END IF;

            SELECT e.document_sha256
            INTO source_digest
            FROM payroll_payment_tax_evidence e
            WHERE e.company_id=NEW.company_id
              AND e.id=NEW.evidence_id;

            SELECT i.evidence_id, i.document_sha256,
                   i.document_size_bytes
            INTO integrity_evidence, verified_digest,
                 verified_size
            FROM payroll_payment_tax_document_integrity i
            WHERE i.company_id=NEW.company_id
              AND i.id=NEW.integrity_check_id;

            IF source_digest IS NULL
               OR integrity_evidence IS DISTINCT FROM NEW.evidence_id
               OR source_digest IS DISTINCT FROM NEW.document_sha256
               OR verified_digest IS DISTINCT FROM NEW.document_sha256
               OR verified_size IS DISTINCT FROM NEW.document_size_bytes
            THEN
                RAISE EXCEPTION 'Custody source mismatch';
            END IF;

            RETURN NEW;
        END;
        $$
    """)

    op.execute("""
        CREATE TRIGGER trg_pptsc_guard
        BEFORE INSERT OR UPDATE OR DELETE
        ON payroll_payment_tax_secure_custody
        FOR EACH ROW EXECUTE FUNCTION pptsc_guard()
    """)

    op.execute("""
        CREATE FUNCTION pptsc_review_guard()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT pptsc_authorized(
                NEW.reviewed_by,
                NEW.company_id,
                'employees.manage'
            ) OR NOT pptsc_authorized(
                NEW.reviewed_by,
                NEW.company_id,
                'journal_entries.approve'
            ) THEN
                RAISE EXCEPTION 'Reviewer lacks company permissions';
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM payroll_payment_tax_secure_custody c
                WHERE c.company_id=NEW.company_id
                  AND c.evidence_id=NEW.evidence_id
                  AND c.integrity_check_id=NEW.integrity_check_id
            ) THEN
                RAISE EXCEPTION 'Secure custody required';
            END IF;

            RETURN NEW;
        END;
        $$
    """)

    op.execute("""
        CREATE TRIGGER trg_pptsc_review_guard
        BEFORE INSERT
        ON payroll_payment_tax_document_reviews
        FOR EACH ROW
        EXECUTE FUNCTION pptsc_review_guard()
    """)


def downgrade():
    op.execute("""
        DROP TRIGGER trg_pptsc_review_guard
        ON payroll_payment_tax_document_reviews
    """)
    op.execute("DROP FUNCTION pptsc_review_guard()")

    op.execute("""
        DROP TRIGGER trg_pptsc_guard
        ON payroll_payment_tax_secure_custody
    """)
    op.execute("DROP FUNCTION pptsc_guard()")
    op.drop_table("payroll_payment_tax_secure_custody")
    op.execute("DROP FUNCTION pptsc_authorized(integer,integer,text)")
