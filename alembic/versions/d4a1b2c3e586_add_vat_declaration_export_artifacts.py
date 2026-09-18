"""Add immutable VAT declaration export artifacts."""

from alembic import op
import sqlalchemy as sa


revision = "d4a1b2c3e586"
down_revision = "c3f0a1b2d475"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "vat_declaration_export_artifacts",
        sa.Column(
            "id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "company_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "vat_declaration_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "declaration_snapshot_version",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "form_code",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "form_version",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "export_format",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "mime_type",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "file_name",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "payload",
            sa.LargeBinary(),
            nullable=False,
        ),
        sa.Column(
            "payload_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "payload_size_bytes",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "official_xsd_verified",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "declaration_snapshot_version > 0",
            name="ck_vdea_snapshot_version_pos",
        ),
        sa.CheckConstraint(
            "form_version > 0",
            name="ck_vdea_form_version_pos",
        ),
        sa.CheckConstraint(
            "payload_size_bytes > 0",
            name="ck_vdea_payload_size_pos",
        ),
        sa.CheckConstraint(
            "char_length(payload_sha256) = 64",
            name="ck_vdea_sha256_len",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_vdea_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["vat_declaration_id"],
            ["vat_declarations.id"],
            name="fk_vdea_declaration",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_vdea_created_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_vat_declaration_export_artifacts",
        ),
        sa.UniqueConstraint(
            "company_id",
            "vat_declaration_id",
            "declaration_snapshot_version",
            "form_code",
            "form_version",
            "export_format",
            name="uq_vdea_identity",
        ),
    )

    op.create_index(
        "ix_vdea_company",
        "vat_declaration_export_artifacts",
        ["company_id"],
        unique=False,
    )

    op.create_index(
        "ix_vdea_declaration",
        "vat_declaration_export_artifacts",
        ["vat_declaration_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_vdea_declaration",
        table_name="vat_declaration_export_artifacts",
    )
    op.drop_index(
        "ix_vdea_company",
        table_name="vat_declaration_export_artifacts",
    )
    op.drop_table(
        "vat_declaration_export_artifacts"
    )
