from datetime import date, datetime
from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class InputVatCreditClaim(Base):
    """Immutable operator attestation + policy decision linked to immutable evidence."""
    __tablename__ = 'input_vat_credit_claims'
    __table_args__ = (
        Index('ix_ivcc_company_calculation', 'company_id', 'tax_calculation_id'),
        CheckConstraint('EXTRACT(DAY FROM claim_period) = 1', name='ck_ivcc_month'),
        CheckConstraint('reversal_evidence_id IS NULL OR reversal_evidence_id <> evidence_id', name='ck_ivcc_distinct_reversal'),
        UniqueConstraint('company_id', 'request_key', name='uq_ivcc_request'),
        UniqueConstraint('evidence_id', name='uq_ivcc_evidence'),
        UniqueConstraint('reversal_evidence_id', name='uq_ivcc_reversal'),
        ForeignKeyConstraint(['company_id', 'evidence_id', 'tax_calculation_id'],
            ['tax_credit_evidence.company_id', 'tax_credit_evidence.id', 'tax_credit_evidence.tax_calculation_id'], name='fk_ivcc_evidence', ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id', 'reversal_evidence_id', 'tax_calculation_id'],
            ['tax_credit_evidence.company_id', 'tax_credit_evidence.id', 'tax_credit_evidence.tax_calculation_id'], name='fk_ivcc_reversal', ondelete='RESTRICT'),
        CheckConstraint("length(trim(request_key)) > 0 AND length(policy_version) > 0", name='ck_ivcc_identifiers'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(nullable=False)
    tax_calculation_id: Mapped[int] = mapped_column(nullable=False)
    evidence_id: Mapped[int] = mapped_column(nullable=False)
    reversal_evidence_id: Mapped[int | None] = mapped_column(nullable=True)
    request_key: Mapped[str] = mapped_column(String(100))
    policy_version: Mapped[str] = mapped_column(String(80))
    claim_period: Mapped[date] = mapped_column(Date)
    attestation: Mapped[dict] = mapped_column(JSONB)
    decision: Mapped[dict] = mapped_column(JSONB)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
