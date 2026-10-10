"""Add employee tax documentary evidence foundation.

Revision ID: 13d4a7b8c012
Revises: 13d4a7b8c011
"""

from alembic import op
import sqlalchemy as sa


revision = "13d4a7b8c012"
down_revision = "13d4a7b8c011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payroll_employee_tax_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("document_type", sa.String(100), nullable=False),
        sa.Column("document_number", sa.String(150), nullable=False),
        sa.Column("issued_on", sa.Date(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("entitlement_type", sa.String(32), nullable=False),
        sa.Column("entitlement_code", sa.String(64), nullable=False),
        sa.Column(
            "verification_status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("verified_by", sa.Integer(), nullable=True),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "employee_id"],
            ["employees.company_id", "employees.id"],
            name="fk_payroll_tax_evidence_employee",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_payroll_tax_evidence_created_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by"],
            ["users.id"],
            name="fk_payroll_tax_evidence_verified_by",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_tax_evidence_company_id_id",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_payroll_tax_evidence_valid_range",
        ),
        sa.CheckConstraint(
            "issued_on <= valid_from",
            name="ck_payroll_tax_evidence_issue_date",
        ),
        sa.CheckConstraint(
            "entitlement_type IN "
            "('benefit','exemption','individual_rate')",
            name="ck_payroll_tax_evidence_entitlement_type",
        ),
        sa.CheckConstraint(
            "verification_status IN "
            "('pending','verified','rejected')",
            name="ck_payroll_tax_evidence_status",
        ),
        sa.CheckConstraint(
            "("
            "verification_status = 'verified' "
            "AND verified_by IS NOT NULL "
            "AND verified_at IS NOT NULL"
            ") OR ("
            "verification_status <> 'verified' "
            "AND verified_by IS NULL "
            "AND verified_at IS NULL"
            ")",
            name="ck_payroll_tax_evidence_verification",
        ),
        sa.CheckConstraint(
            "length(trim(document_type)) > 0 "
            "AND length(trim(document_number)) > 0 "
            "AND length(trim(entitlement_code)) > 0",
            name="ck_payroll_tax_evidence_nonempty",
        ),
    )

    op.create_index(
        "ix_payroll_tax_evidence_employee_valid",
        "payroll_employee_tax_evidence",
        ["company_id", "employee_id", "valid_from"],
    )

    op.create_index(
        "ix_payroll_tax_evidence_entitlement",
        "payroll_employee_tax_evidence",
        ["company_id", "entitlement_type", "entitlement_code"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_payroll_tax_evidence_entitlement",
        table_name="payroll_employee_tax_evidence",
    )

    op.drop_index(
        "ix_payroll_tax_evidence_employee_valid",
        table_name="payroll_employee_tax_evidence",
    )

    op.drop_table("payroll_employee_tax_evidence")
