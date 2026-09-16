"""add effective dated VAT settings and line legal basis

Revision ID: e2a9c5d7f046
Revises: d1f8b4c6e935
Create Date: 2026-09-16 08:07:42.594453

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2a9c5d7f046'
down_revision: Union[str, Sequence[str], None] = 'd1f8b4c6e935'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('company_vat_policies',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('effective_from', sa.Date(), nullable=False),
    sa.Column('payer_status', sa.String(length=20), nullable=False),
    sa.Column('vat_number', sa.String(length=20), nullable=True),
    sa.Column('legal_basis', sa.String(length=500), nullable=False),
    sa.Column('allow_cash_method', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(payer_status = 'vat_payer' AND vat_number IS NOT NULL AND length(trim(vat_number)) > 0) OR (payer_status = 'non_vat_payer' AND vat_number IS NULL AND NOT allow_cash_method)", name='ck_cvp_registration'),
    sa.CheckConstraint("payer_status IN ('vat_payer', 'non_vat_payer')", name='ck_cvp_status'),
    sa.CheckConstraint('length(trim(legal_basis)) > 0', name='ck_cvp_basis'),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('company_id', 'effective_from', name='uq_cvp_company_date'),
    sa.UniqueConstraint('company_id', 'id', name='uq_cvp_company_id')
    )
    op.create_table('counterparty_vat_registrations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('counterparty_id', sa.Integer(), nullable=False),
    sa.Column('effective_from', sa.Date(), nullable=False),
    sa.Column('payer_status', sa.String(length=20), nullable=False),
    sa.Column('vat_number', sa.String(length=20), nullable=True),
    sa.Column('legal_basis', sa.String(length=500), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(payer_status = 'vat_payer' AND vat_number IS NOT NULL AND length(trim(vat_number)) > 0) OR (payer_status = 'non_vat_payer' AND vat_number IS NULL)", name='ck_cvr_registration'),
    sa.CheckConstraint("payer_status IN ('vat_payer', 'non_vat_payer')", name='ck_cvr_status'),
    sa.CheckConstraint('length(trim(legal_basis)) > 0', name='ck_cvr_basis'),
    sa.ForeignKeyConstraint(['company_id', 'counterparty_id'], ['counterparties.company_id', 'counterparties.id'], name='fk_cvr_party', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('company_id', 'counterparty_id', 'effective_from', name='uq_cvr_party_date'),
    sa.UniqueConstraint('company_id', 'counterparty_id', 'id', name='uq_cvr_party_id')
    )
    op.add_column('companies', sa.Column('vat_policy_enabled', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('trade_document_lines', sa.Column('tax_legal_basis', sa.String(length=500), nullable=True))
    op.add_column('trade_document_lines', sa.Column('no_vat_reason', sa.String(length=30), nullable=True))
    op.create_check_constraint('ck_tdl_no_vat_reason', 'trade_document_lines', "no_vat_reason IS NULL OR (no_vat_reason = 'non_vat_payer' AND tax_rate_code IS NULL AND tax_legal_basis IS NOT NULL)")
    op.create_check_constraint('ck_tdl_vat_basis', 'trade_document_lines', 'tax_legal_basis IS NULL OR length(trim(tax_legal_basis)) > 0')
    op.add_column('trade_documents', sa.Column('counterparty_vat_registration_id', sa.Integer(), nullable=True))
    op.add_column('trade_documents', sa.Column('vat_policy_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_trade_document_vat_policy', 'trade_documents', 'company_vat_policies', ['company_id', 'vat_policy_id'], ['company_id', 'id'], ondelete='RESTRICT')
    op.create_foreign_key('fk_td_vat_registration', 'trade_documents', 'counterparty_vat_registrations', ['company_id', 'counterparty_id', 'counterparty_vat_registration_id'], ['company_id', 'counterparty_id', 'id'], ondelete='RESTRICT')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_td_vat_registration', 'trade_documents', type_='foreignkey')
    op.drop_constraint('fk_trade_document_vat_policy', 'trade_documents', type_='foreignkey')
    op.drop_column('trade_documents', 'vat_policy_id')
    op.drop_column('trade_documents', 'counterparty_vat_registration_id')
    op.drop_constraint('ck_tdl_vat_basis', 'trade_document_lines', type_='check')
    op.drop_constraint('ck_tdl_no_vat_reason', 'trade_document_lines', type_='check')
    op.drop_column('trade_document_lines', 'no_vat_reason')
    op.drop_column('trade_document_lines', 'tax_legal_basis')
    op.drop_column('companies', 'vat_policy_enabled')
    op.drop_table('counterparty_vat_registrations')
    op.drop_table('company_vat_policies')
