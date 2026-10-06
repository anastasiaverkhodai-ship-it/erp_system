"""repair payroll advance journal exclusivity

Revision ID: 83d21036fabe
Revises: b082b6582130
Create Date: 2026-10-06 10:12:20.408112

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '83d21036fabe'
down_revision: Union[str, Sequence[str], None] = 'b082b6582130'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_je_payroll_calculation_exclusive",
        "journal_entries",
        type_="check",
    )
    op.drop_constraint(
        "ck_je_payroll_disbursement_exclusive",
        "journal_entries",
        type_="check",
    )

    op.create_check_constraint(
        "ck_je_payroll_calculation_exclusive",
        "journal_entries",
        "payroll_calculation_id IS NULL OR "
        "num_nonnulls("
        "payroll_advance_id,"
        "payroll_disbursement_id,"
        "customer_advance_clearing_event_id,"
        "document_id,"
        "fixed_asset_commissioning_id,"
        "input_vat_fulfillment_bridge_event_id,"
        "opening_balance_id,"
        "payment_id,"
        "payment_settlement_allocation_id,"
        "purchase_return_input_vat_credit_correction_event_id,"
        "purchase_return_recognition_event_id,"
        "purchase_return_vat_adjustment_event_id,"
        "purchase_value_correction_fifo_impact_event_id,"
        "purchase_value_correction_input_vat_credit_correction_event_id,"
        "purchase_value_correction_ma_replay_event_id,"
        "purchase_value_correction_vat_adjustment_event_id,"
        "sales_recognition_event_id,"
        "sales_return_cost_restoration_event_id,"
        "sales_return_recognition_event_id,"
        "supplier_advance_clearing_event_id,"
        "tax_invoice_correction_line_id,"
        "tax_recognition_event_id,"
        "vat_advance_bridge_event_id,"
        "year_end_closing_id"
        ") = 0",
    )

    op.create_check_constraint(
        "ck_je_payroll_disbursement_exclusive",
        "journal_entries",
        "payroll_disbursement_id IS NULL OR "
        "num_nonnulls("
        "payroll_advance_id,"
        "payroll_calculation_id,"
        "customer_advance_clearing_event_id,"
        "document_id,"
        "fixed_asset_commissioning_id,"
        "input_vat_fulfillment_bridge_event_id,"
        "opening_balance_id,"
        "payment_id,"
        "payment_settlement_allocation_id,"
        "purchase_return_input_vat_credit_correction_event_id,"
        "purchase_return_recognition_event_id,"
        "purchase_return_vat_adjustment_event_id,"
        "purchase_value_correction_fifo_impact_event_id,"
        "purchase_value_correction_input_vat_credit_correction_event_id,"
        "purchase_value_correction_ma_replay_event_id,"
        "purchase_value_correction_vat_adjustment_event_id,"
        "sales_recognition_event_id,"
        "sales_return_cost_restoration_event_id,"
        "sales_return_recognition_event_id,"
        "supplier_advance_clearing_event_id,"
        "tax_invoice_correction_line_id,"
        "tax_recognition_event_id,"
        "vat_advance_bridge_event_id,"
        "year_end_closing_id"
        ") = 0",
    )

    op.create_check_constraint(
        "ck_je_payroll_advance_exclusive",
        "journal_entries",
        "payroll_advance_id IS NULL OR "
        "num_nonnulls("
        "payroll_calculation_id,"
        "payroll_disbursement_id,"
        "customer_advance_clearing_event_id,"
        "document_id,"
        "fixed_asset_commissioning_id,"
        "input_vat_fulfillment_bridge_event_id,"
        "opening_balance_id,"
        "payment_id,"
        "payment_settlement_allocation_id,"
        "purchase_return_input_vat_credit_correction_event_id,"
        "purchase_return_recognition_event_id,"
        "purchase_return_vat_adjustment_event_id,"
        "purchase_value_correction_fifo_impact_event_id,"
        "purchase_value_correction_input_vat_credit_correction_event_id,"
        "purchase_value_correction_ma_replay_event_id,"
        "purchase_value_correction_vat_adjustment_event_id,"
        "sales_recognition_event_id,"
        "sales_return_cost_restoration_event_id,"
        "sales_return_recognition_event_id,"
        "supplier_advance_clearing_event_id,"
        "tax_invoice_correction_line_id,"
        "tax_recognition_event_id,"
        "vat_advance_bridge_event_id,"
        "year_end_closing_id"
        ") = 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_je_payroll_advance_exclusive",
        "journal_entries",
        type_="check",
    )
    op.drop_constraint(
        "ck_je_payroll_disbursement_exclusive",
        "journal_entries",
        type_="check",
    )
    op.drop_constraint(
        "ck_je_payroll_calculation_exclusive",
        "journal_entries",
        type_="check",
    )

    op.create_check_constraint(
        "ck_je_payroll_calculation_exclusive",
        "journal_entries",
        "payroll_calculation_id IS NULL OR "
        "num_nonnulls("
        "payroll_disbursement_id,"
        "customer_advance_clearing_event_id,"
        "document_id,"
        "fixed_asset_commissioning_id,"
        "input_vat_fulfillment_bridge_event_id,"
        "opening_balance_id,"
        "payment_id,"
        "payment_settlement_allocation_id,"
        "purchase_return_input_vat_credit_correction_event_id,"
        "purchase_return_recognition_event_id,"
        "purchase_return_vat_adjustment_event_id,"
        "purchase_value_correction_fifo_impact_event_id,"
        "purchase_value_correction_input_vat_credit_correction_event_id,"
        "purchase_value_correction_ma_replay_event_id,"
        "purchase_value_correction_vat_adjustment_event_id,"
        "sales_recognition_event_id,"
        "sales_return_cost_restoration_event_id,"
        "sales_return_recognition_event_id,"
        "supplier_advance_clearing_event_id,"
        "tax_invoice_correction_line_id,"
        "tax_recognition_event_id,"
        "vat_advance_bridge_event_id,"
        "year_end_closing_id"
        ") = 0",
    )

    op.create_check_constraint(
        "ck_je_payroll_disbursement_exclusive",
        "journal_entries",
        "payroll_disbursement_id IS NULL OR "
        "num_nonnulls("
        "payroll_calculation_id,"
        "customer_advance_clearing_event_id,"
        "document_id,"
        "fixed_asset_commissioning_id,"
        "input_vat_fulfillment_bridge_event_id,"
        "opening_balance_id,"
        "payment_id,"
        "payment_settlement_allocation_id,"
        "purchase_return_input_vat_credit_correction_event_id,"
        "purchase_return_recognition_event_id,"
        "purchase_return_vat_adjustment_event_id,"
        "purchase_value_correction_fifo_impact_event_id,"
        "purchase_value_correction_input_vat_credit_correction_event_id,"
        "purchase_value_correction_ma_replay_event_id,"
        "purchase_value_correction_vat_adjustment_event_id,"
        "sales_recognition_event_id,"
        "sales_return_cost_restoration_event_id,"
        "sales_return_recognition_event_id,"
        "supplier_advance_clearing_event_id,"
        "tax_invoice_correction_line_id,"
        "tax_recognition_event_id,"
        "vat_advance_bridge_event_id,"
        "year_end_closing_id"
        ") = 0",
    )
