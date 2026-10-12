"""Phase 13 C3-B2: durable deduction remainder schema.

These models define technical ledger storage only.

They do not authorize payroll withholding, establish legal
entitlement, create accounting postings, or settle liabilities.
"""

import sqlalchemy as sa

from app.core.database import Base


class PayrollDeductionRemainder(Base):
    __tablename__ = "payroll_deduction_remainders"

    id = sa.Column(
        sa.Integer,
        sa.Identity(),
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

    original_amount = sa.Column(
        sa.Numeric(18, 2),
        nullable=False,
    )

    currency_code = sa.Column(
        sa.String(3),
        nullable=False,
    )

    created_by = sa.Column(
        sa.Integer,
        sa.ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    created_at = sa.Column(
        sa.DateTime(timezone=True),
        nullable=False,
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["company_id", "instruction_id"],
            [
                "payroll_deduction_instructions.company_id",
                "payroll_deduction_instructions.id",
            ],
            name="fk_pdr_company_instruction",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id",
            "instruction_id",
            name="uq_pdr_company_instruction",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_pdr_company_id_id",
        ),
        sa.CheckConstraint(
            "original_amount >= 0",
            name="ck_pdr_original_nonnegative",
        ),
        sa.CheckConstraint(
            "currency_code = upper(currency_code) "
            "AND char_length(currency_code) = 3",
            name="ck_pdr_currency",
        ),
    )


class PayrollDeductionRemainderEvent(Base):
    __tablename__ = "payroll_deduction_remainder_events"

    id = sa.Column(
        sa.Integer,
        sa.Identity(),
        primary_key=True,
    )

    company_id = sa.Column(
        sa.Integer,
        nullable=False,
    )

    remainder_id = sa.Column(
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
        sa.ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    created_at = sa.Column(
        sa.DateTime(timezone=True),
        nullable=False,
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["company_id", "remainder_id"],
            [
                "payroll_deduction_remainders.company_id",
                "payroll_deduction_remainders.id",
            ],
            name="fk_pdre_company_remainder",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id",
            "event_id",
            name="uq_pdre_company_event",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_pdre_company_id_id",
        ),
        sa.CheckConstraint(
            "amount > 0",
            name="ck_pdre_positive_amount",
        ),
        sa.CheckConstraint(
            "char_length(trim(event_id)) > 0 "
            "AND event_id = trim(event_id)",
            name="ck_pdre_event_id",
        ),
        sa.Index(
            "ix_pdre_company_remainder_created",
            "company_id",
            "remainder_id",
            "created_at",
            "id",
        ),
    )
