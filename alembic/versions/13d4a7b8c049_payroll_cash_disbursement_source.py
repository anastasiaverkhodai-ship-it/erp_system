"""Add mutually exclusive bank or cash source to payroll disbursements.

Revision ID: 13d4a7b8c049
Revises: 13d4a7b8c048
"""

from alembic import op
import sqlalchemy as sa

revision = "13d4a7b8c049"
down_revision = "13d4a7b8c048"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "payroll_disbursements",
        sa.Column("cash_desk_id", sa.Integer(), nullable=True),
    )
    op.alter_column(
        "payroll_disbursements",
        "bank_account_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.alter_column(
        "payroll_disbursements",
        "employee_iban_snapshot",
        existing_type=sa.String(length=34),
        nullable=True,
    )
    op.create_foreign_key(
        "fk_payroll_disbursements_company_cash_desk",
        "payroll_disbursements",
        "cash_desks",
        ["company_id", "cash_desk_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_payroll_disbursement_payment_source",
        "payroll_disbursements",
        "num_nonnulls(bank_account_id, cash_desk_id) = 1",
    )
    op.create_check_constraint(
        "ck_payroll_disbursement_payment_identity",
        "payroll_disbursements",
        "(bank_account_id IS NOT NULL "
        "AND employee_iban_snapshot IS NOT NULL) "
        "OR (cash_desk_id IS NOT NULL "
        "AND employee_iban_snapshot IS NULL)",
    )


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
            "Cannot downgrade: existing cash payroll disbursements "
            "must be preserved"
        )

    op.drop_constraint(
        "ck_payroll_disbursement_payment_identity",
        "payroll_disbursements",
        type_="check",
    )
    op.drop_constraint(
        "ck_payroll_disbursement_payment_source",
        "payroll_disbursements",
        type_="check",
    )
    op.drop_constraint(
        "fk_payroll_disbursements_company_cash_desk",
        "payroll_disbursements",
        type_="foreignkey",
    )
    op.alter_column(
        "payroll_disbursements",
        "employee_iban_snapshot",
        existing_type=sa.String(length=34),
        nullable=False,
    )
    op.alter_column(
        "payroll_disbursements",
        "bank_account_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.drop_column("payroll_disbursements", "cash_desk_id")
