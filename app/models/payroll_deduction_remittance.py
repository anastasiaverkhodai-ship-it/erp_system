"""Phase 13 C4-B2: immutable technical remittance storage.

These records represent candidate allocations only.
They are not bank transfers or payroll postings.

Legal authorization: NOT_AUTHORIZED.
"""

import sqlalchemy as sa

from app.core.database import Base


class PayrollDeductionRemittancePlan(Base):
    __tablename__ = "payroll_deduction_remittance_plans"

    id = sa.Column(
        sa.Integer,
        primary_key=True,
    )

    company_id = sa.Column(
        sa.Integer,
        nullable=False,
    )

    instruction_id = sa.Column(
        sa.Integer,
        nullable=False,
    )

    beneficiary_reference = sa.Column(
        sa.String(120),
        nullable=False,
    )

    currency_code = sa.Column(
        sa.String(3),
        nullable=False,
    )

    available_amount = sa.Column(
        sa.Numeric(18, 2),
        nullable=False,
    )

    created_by = sa.Column(
        sa.Integer,
        sa.ForeignKey("users.id"),
        nullable=False,
    )

    created_at = sa.Column(
        sa.DateTime,
        nullable=False,
        server_default=sa.text("now()"),
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["company_id", "instruction_id"],
            [
                "payroll_deduction_instructions.company_id",
                "payroll_deduction_instructions.id",
            ],
            name="fk_pdrp_company_instruction",
        ),
        sa.UniqueConstraint(
            "company_id",
            "instruction_id",
            name="uq_pdrp_company_instruction",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_pdrp_company_id_id",
        ),
        sa.CheckConstraint(
            "available_amount >= 0",
            name="ck_pdrp_available_nonnegative",
        ),
        sa.CheckConstraint(
            "currency_code = upper(currency_code) "
            "AND char_length(currency_code) = 3",
            name="ck_pdrp_currency",
        ),
        sa.CheckConstraint(
            "char_length(trim(beneficiary_reference)) > 0 "
            "AND beneficiary_reference = trim(beneficiary_reference)",
            name="ck_pdrp_beneficiary",
        ),
        sa.Index(
            "ix_pdrp_company_beneficiary",
            "company_id",
            "beneficiary_reference",
        ),
    )


class PayrollDeductionRemittanceEvent(Base):
    __tablename__ = "payroll_deduction_remittance_events"

    id = sa.Column(
        sa.Integer,
        primary_key=True,
    )

    company_id = sa.Column(
        sa.Integer,
        nullable=False,
    )

    plan_id = sa.Column(
        sa.Integer,
        nullable=False,
    )

    event_id = sa.Column(
        sa.String(100),
        nullable=False,
    )

    amount = sa.Column(
        sa.Numeric(18, 2),
        nullable=False,
    )

    created_by = sa.Column(
        sa.Integer,
        sa.ForeignKey("users.id"),
        nullable=False,
    )

    created_at = sa.Column(
        sa.DateTime,
        nullable=False,
        server_default=sa.text("now()"),
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["company_id", "plan_id"],
            [
                "payroll_deduction_remittance_plans.company_id",
                "payroll_deduction_remittance_plans.id",
            ],
            name="fk_pdrevent_company_plan",
        ),
        sa.UniqueConstraint(
            "company_id",
            "event_id",
            name="uq_pdrevent_company_event",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_pdrevent_company_id_id",
        ),
        sa.CheckConstraint(
            "amount > 0",
            name="ck_pdrevent_positive_amount",
        ),
        sa.CheckConstraint(
            "char_length(trim(event_id)) > 0 "
            "AND event_id = trim(event_id)",
            name="ck_pdrevent_event_id",
        ),
        sa.Index(
            "ix_pdrevent_company_plan_created",
            "company_id",
            "plan_id",
            "created_at",
            "id",
        ),
    )
