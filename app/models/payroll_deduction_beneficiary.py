"""Versioned, independently verified recipient details for deduction execution."""
import sqlalchemy as sa
from app.core.database import Base


class PayrollDeductionBeneficiary(Base):
    __tablename__ = 'payroll_deduction_beneficiaries'
    id = sa.Column(sa.Integer, primary_key=True)
    company_id = sa.Column(sa.Integer, nullable=False)
    instruction_id = sa.Column(sa.Integer, nullable=False)
    request_key = sa.Column(sa.String(100), nullable=False)
    recipient_name = sa.Column(sa.String(255), nullable=False)
    recipient_identifier = sa.Column(sa.String(100), nullable=False)
    iban = sa.Column(sa.String(29), nullable=True)
    settlement_kind = sa.Column(sa.String(30), nullable=False)
    account_id = sa.Column(sa.Integer, nullable=False)
    document_reference = sa.Column(sa.String(500), nullable=False)
    valid_from = sa.Column(sa.Date, nullable=False)
    valid_to = sa.Column(sa.Date, nullable=True)
    status = sa.Column(sa.String(20), nullable=False, server_default='pending')
    created_by = sa.Column(sa.Integer, sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    created_at = sa.Column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    verified_by = sa.Column(sa.Integer, sa.ForeignKey('users.id', ondelete='RESTRICT'))
    verified_at = sa.Column(sa.DateTime(timezone=True))
    revoked_by = sa.Column(sa.Integer, sa.ForeignKey('users.id', ondelete='RESTRICT'))
    revoked_at = sa.Column(sa.DateTime(timezone=True))
    __table_args__ = (
        sa.UniqueConstraint('company_id', 'id', name='uq_pdb_company_id'),
        sa.UniqueConstraint('company_id', 'request_key', name='uq_pdb_request'),
        sa.ForeignKeyConstraint(['company_id', 'instruction_id'],
            ['payroll_deduction_instructions.company_id', 'payroll_deduction_instructions.id'],
            name='fk_pdb_instruction', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['company_id', 'account_id'], ['accounts.company_id', 'accounts.id'],
            name='fk_pdb_account', ondelete='RESTRICT'),
        sa.CheckConstraint("settlement_kind IN ('recipient_payment', 'employer_recovery')", name='ck_pdb_kind'),
        sa.CheckConstraint("(settlement_kind = 'recipient_payment' AND iban IS NOT NULL AND iban ~ '^UA[0-9]{27}$') OR (settlement_kind = 'employer_recovery' AND iban IS NULL)", name='ck_pdb_iban'),
        sa.CheckConstraint('valid_to IS NULL OR valid_to >= valid_from', name='ck_pdb_dates'),
        sa.CheckConstraint("length(trim(request_key)) > 0 AND length(trim(recipient_name)) > 0 AND length(trim(recipient_identifier)) > 0 AND length(trim(document_reference)) > 0", name='ck_pdb_text'),
        sa.CheckConstraint("(status = 'pending' AND verified_by IS NULL AND verified_at IS NULL AND revoked_by IS NULL AND revoked_at IS NULL) OR (status = 'verified' AND verified_by IS NOT NULL AND verified_at IS NOT NULL AND revoked_by IS NULL AND revoked_at IS NULL) OR (status = 'revoked' AND verified_by IS NOT NULL AND verified_at IS NOT NULL AND revoked_by IS NOT NULL AND revoked_at IS NOT NULL AND revoked_at >= verified_at)", name='ck_pdb_lifecycle'),
        sa.CheckConstraint('verified_by IS NULL OR verified_by <> created_by', name='ck_pdb_four_eyes'),
        sa.Index('ix_pdb_instruction_dates', 'company_id', 'instruction_id', 'valid_from'),
    )
