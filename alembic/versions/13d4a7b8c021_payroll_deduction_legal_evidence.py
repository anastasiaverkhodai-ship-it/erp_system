"""Add payroll deduction legal evidence.

Revision ID: 13d4a7b8c021
Revises: 13d4a7b8c020
"""

from alembic import op
import sqlalchemy as sa


revision = "13d4a7b8c021"
down_revision = "13d4a7b8c020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payroll_deduction_legal_evidence",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column(
            "payroll_deduction_instruction_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "legal_category",
            sa.String(length=50),
            nullable=False,
        ),
        sa.Column(
            "document_reference",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "verification_status",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "verified_by",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_pdle_company_id_id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "payroll_deduction_instruction_id",
            name="uq_pdle_instruction",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "payroll_deduction_instruction_id",
            ],
            [
                "payroll_deduction_instructions.company_id",
                "payroll_deduction_instructions.id",
            ],
            name="fk_pdle_instruction",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "legal_category IN ("
            "'employer_statutory', "
            "'enforcement_other', "
            "'enforcement_special', "
            "'minor_child_alimony'"
            ")",
            name="ck_pdle_category",
        ),
        sa.CheckConstraint(
            "char_length(trim(document_reference)) > 0",
            name="ck_pdle_document",
        ),
        sa.CheckConstraint(
            "verification_status IN "
            "('pending', 'verified', 'rejected')",
            name="ck_pdle_status",
        ),
        sa.CheckConstraint(
            "(verification_status = 'verified' "
            "AND verified_by IS NOT NULL "
            "AND verified_at IS NOT NULL) "
            "OR verification_status <> 'verified'",
            name="ck_pdle_verified",
        ),
    )


def downgrade() -> None:
    # Serialize against inserts while deciding whether history can be removed.
    op.execute("LOCK TABLE payroll_deduction_legal_evidence IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM payroll_deduction_legal_evidence)"
    )).scalar():
        raise RuntimeError("Cannot downgrade deduction evidence with historical records")
    op.drop_table("payroll_deduction_legal_evidence")
