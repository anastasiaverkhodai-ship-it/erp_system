"""Operational deduction assessments, actual payment withholdings and remittances."""
import sqlalchemy as sa
from app.core.database import Base


def fk(local, remote, name):
    return sa.ForeignKeyConstraint(['company_id', local], [remote+'.company_id', remote+'.id'], name=name, ondelete='RESTRICT')


class PayrollDeductionAssessment(Base):
    __tablename__ = 'payroll_deduction_assessments'
    id = sa.Column(sa.Integer, primary_key=True)
    company_id = sa.Column(sa.Integer, nullable=False)
    instruction_id = sa.Column(sa.Integer, nullable=False)
    beneficiary_id = sa.Column(sa.Integer, nullable=False)
    rule_approval_id = sa.Column(sa.Integer, nullable=False)
    reference = sa.Column(sa.String(500), nullable=False)
    request_key = sa.Column(sa.String(100), nullable=False)
    amount = sa.Column(sa.Numeric(18,2), nullable=False)
    priority = sa.Column(sa.Integer, nullable=False)
    priority_reference = sa.Column(sa.String(500), nullable=False)
    individual_limit_percentage = sa.Column(sa.Numeric(7,4), nullable=False)
    effective_from = sa.Column(sa.Date, nullable=False)
    created_by = sa.Column(sa.Integer, sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    created_at = sa.Column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    __table_args__ = (
        sa.UniqueConstraint('company_id','id',name='uq_pda_company'),
        sa.UniqueConstraint('company_id','request_key',name='uq_pda_request'),
        sa.UniqueConstraint('company_id','instruction_id','reference',name='uq_pda_reference'),
        fk('instruction_id','payroll_deduction_instructions','fk_pda_instruction'),
        fk('beneficiary_id','payroll_deduction_beneficiaries','fk_pda_beneficiary'),
        fk('rule_approval_id','payroll_deduction_rule_approvals','fk_pda_rule'),
        sa.CheckConstraint("amount > 0 AND amount <> 'NaN'::numeric",name='ck_pda_amount'),
        sa.CheckConstraint('priority >= 0 AND individual_limit_percentage > 0 AND individual_limit_percentage <= 70',name='ck_pda_limits'),
        sa.CheckConstraint('length(trim(reference)) > 0 AND length(trim(request_key)) > 0 AND length(trim(priority_reference)) > 0',name='ck_pda_references'),
    )


class PayrollWithholdingBatch(Base):
    __tablename__ = 'payroll_withholding_batches'
    id = sa.Column(sa.Integer, primary_key=True)
    company_id = sa.Column(sa.Integer, nullable=False)
    advance_id = sa.Column(sa.Integer)
    disbursement_id = sa.Column(sa.Integer)
    employee_id = sa.Column(sa.Integer, nullable=False)
    payment_date = sa.Column(sa.Date, nullable=False)
    net_before = sa.Column(sa.Numeric(18,2), nullable=False)
    protected_amount = sa.Column(sa.Numeric(18,2), nullable=False)
    withheld_amount = sa.Column(sa.Numeric(18,2), nullable=False)
    income_reference = sa.Column(sa.String(500), nullable=False)
    request_key = sa.Column(sa.String(100), nullable=False)
    request_fingerprint = sa.Column(sa.String(64), nullable=False)
    created_by = sa.Column(sa.Integer, sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    creation_xid = sa.Column(sa.BigInteger, nullable=False, server_default=sa.text('txid_current()'))
    created_at = sa.Column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    __table_args__ = (
        sa.UniqueConstraint('company_id','id',name='uq_pwb_company'),
        sa.UniqueConstraint('company_id','request_key',name='uq_pwb_request'),
        sa.UniqueConstraint('company_id','advance_id',name='uq_pwb_advance'),
        sa.UniqueConstraint('company_id','disbursement_id',name='uq_pwb_disbursement'),
        fk('advance_id','payroll_advances','fk_pwb_advance'),
        fk('disbursement_id','payroll_disbursements','fk_pwb_disbursement'),
        fk('employee_id','employees','fk_pwb_employee'),
        sa.CheckConstraint('num_nonnulls(advance_id,disbursement_id) = 1',name='ck_pwb_source'),
        sa.CheckConstraint("net_before > 0 AND net_before <> 'NaN'::numeric AND protected_amount >= 0 AND protected_amount <= net_before AND withheld_amount >= 0 AND withheld_amount <= net_before - protected_amount",name='ck_pwb_amounts'),
        sa.CheckConstraint('length(trim(income_reference)) > 0 AND length(trim(request_key)) > 0',name='ck_pwb_reference'),
    )


class PayrollWithholdingLine(Base):
    __tablename__ = 'payroll_withholding_lines'
    id = sa.Column(sa.Integer, primary_key=True)
    company_id = sa.Column(sa.Integer, nullable=False)
    batch_id = sa.Column(sa.Integer, nullable=False)
    assessment_id = sa.Column(sa.Integer, nullable=False)
    requested_amount = sa.Column(sa.Numeric(18,2), nullable=False)
    amount = sa.Column(sa.Numeric(18,2), nullable=False)
    __table_args__ = (
        sa.UniqueConstraint('company_id','id',name='uq_pwl_company'),
        sa.UniqueConstraint('company_id','batch_id','assessment_id',name='uq_pwl_assessment'),
        fk('batch_id','payroll_withholding_batches','fk_pwl_batch'),
        fk('assessment_id','payroll_deduction_assessments','fk_pwl_assessment'),
        sa.CheckConstraint("requested_amount >= 0 AND requested_amount <> 'NaN'::numeric AND amount >= 0 AND amount <= requested_amount",name='ck_pwl_amount'),
    )


class PayrollRecipientRemittance(Base):
    __tablename__ = 'payroll_recipient_remittances'
    id = sa.Column(sa.Integer, primary_key=True)
    company_id = sa.Column(sa.Integer, nullable=False)
    beneficiary_id = sa.Column(sa.Integer, nullable=False)
    payment_beneficiary_id = sa.Column(sa.Integer)
    journal_entry_id = sa.Column(sa.Integer, nullable=False)
    bank_statement_line_id = sa.Column(sa.Integer, nullable=False)
    amount = sa.Column(sa.Numeric(18,2), nullable=False)
    request_key = sa.Column(sa.String(100), nullable=False)
    request_fingerprint = sa.Column(sa.String(64), nullable=False)
    created_by = sa.Column(sa.Integer, sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    creation_xid = sa.Column(sa.BigInteger, nullable=False, server_default=sa.text('txid_current()'))
    created_at = sa.Column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    __table_args__ = (
        sa.UniqueConstraint('company_id','id',name='uq_prr_company'),
        sa.UniqueConstraint('company_id','request_key',name='uq_prr_request'),
        sa.UniqueConstraint('company_id','journal_entry_id',name='uq_prr_journal'),
        sa.UniqueConstraint('company_id','bank_statement_line_id',name='uq_prr_bank_line'),
        fk('beneficiary_id','payroll_deduction_beneficiaries','fk_prr_beneficiary'),
        fk('payment_beneficiary_id','payroll_deduction_beneficiaries','fk_prr_payment_beneficiary'),
        fk('journal_entry_id','journal_entries','fk_prr_journal'),
        fk('bank_statement_line_id','bank_statement_lines','fk_prr_bank_line'),
        sa.CheckConstraint("amount > 0 AND amount <> 'NaN'::numeric",name='ck_prr_amount'),
    )


class PayrollRecipientRemittanceLine(Base):
    __tablename__ = 'payroll_recipient_remittance_lines'
    id = sa.Column(sa.Integer, primary_key=True)
    company_id = sa.Column(sa.Integer, nullable=False)
    remittance_id = sa.Column(sa.Integer, nullable=False)
    withholding_line_id = sa.Column(sa.Integer, nullable=False)
    amount = sa.Column(sa.Numeric(18,2), nullable=False)
    __table_args__ = (
        sa.UniqueConstraint('company_id','remittance_id','withholding_line_id',name='uq_prrl_source'),
        fk('remittance_id','payroll_recipient_remittances','fk_prrl_remittance'),
        fk('withholding_line_id','payroll_withholding_lines','fk_prrl_withholding'),
        sa.CheckConstraint("amount > 0 AND amount <> 'NaN'::numeric",name='ck_prrl_amount'),
    )


class PayrollDeductionAssessmentClosure(Base):
    """Dated closure of the unused part; original withholding history remains intact."""
    __tablename__ = 'payroll_deduction_assessment_closures'
    id = sa.Column(sa.Integer, primary_key=True)
    company_id = sa.Column(sa.Integer, nullable=False)
    assessment_id = sa.Column(sa.Integer, nullable=False)
    effective_from = sa.Column(sa.Date, nullable=False)
    reason = sa.Column(sa.String(500), nullable=False)
    request_key = sa.Column(sa.String(100), nullable=False)
    request_fingerprint = sa.Column(sa.String(64), nullable=False)
    replacement_assessment_id = sa.Column(sa.Integer)
    created_by = sa.Column(sa.Integer, sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    created_at = sa.Column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    __table_args__ = (
        sa.UniqueConstraint('company_id','assessment_id',name='uq_pdac_assessment'),
        sa.UniqueConstraint('company_id','request_key',name='uq_pdac_request'),
        fk('assessment_id','payroll_deduction_assessments','fk_pdac_assessment'),
        fk('replacement_assessment_id','payroll_deduction_assessments','fk_pdac_replacement'),
        sa.CheckConstraint('length(trim(reason)) > 0 AND length(trim(request_key)) > 0',name='ck_pdac_reason'),
        sa.CheckConstraint('replacement_assessment_id IS NULL OR replacement_assessment_id <> assessment_id',name='ck_pdac_replacement'),
    )
