"""Source-bound physical cash GL authorization.

A typed source ID alone never authorizes physical cash GL.
Lifecycle context is internal service provenance, not a database
security boundary. Direct SQL protection requires a separate gate.
"""

from contextlib import contextmanager
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cash_desk import CashDesk
from app.models.payment import Payment
from app.models.payroll_disbursement import PayrollDisbursement


class DirectCashJournalSourceError(ValueError):
    pass


_SOURCE_FIELDS = (
    "document_id", "payment_id", "payroll_disbursement_id",
    "payroll_advance_id", "payroll_calculation_id",
    "payroll_deduction_result_id",
    "payment_settlement_allocation_id",
    "tax_recognition_event_id", "sales_recognition_event_id",
    "vat_advance_bridge_event_id",
    "input_vat_fulfillment_bridge_event_id",
    "supplier_advance_clearing_event_id",
    "customer_advance_clearing_event_id",
    "sales_return_recognition_event_id",
    "sales_return_cost_restoration_event_id",
    "purchase_return_recognition_event_id",
    "purchase_return_vat_adjustment_event_id",
    "purchase_return_input_vat_credit_correction_event_id",
    "purchase_value_correction_fifo_impact_event_id",
    "purchase_value_correction_ma_replay_event_id",
    "purchase_value_correction_vat_adjustment_event_id",
    "purchase_value_correction_input_vat_credit_correction_event_id",
    "tax_invoice_correction_line_id", "opening_balance_id",
    "year_end_closing_id", "fixed_asset_commissioning_id",
    "fixed_asset_depreciation_id", "fixed_asset_disposal_id",
    "fixed_asset_repair_improvement_id",
    "fixed_asset_revaluation_impairment_id",
)


@contextmanager
def cash_gl_lifecycle_scope(db, kind, source_id, action):
    """Bound to a single caller-owned SQLAlchemy session."""
    key = "_erp_cash_gl_lifecycle_scope"
    if db.info.get(key) is not None:
        raise DirectCashJournalSourceError(
            "Nested cash GL lifecycle authorization is forbidden"
        )
    if (
        kind not in ("payment", "payroll")
        or action not in ("post", "reverse")
        or type(source_id) is not int
        or source_id <= 0
    ):
        raise DirectCashJournalSourceError(
            "Invalid cash GL lifecycle scope"
        )

    db.info[key] = (kind, source_id, action)
    try:
        yield
    finally:
        db.info.pop(key, None)


async def require_typed_cash_journal_source(
    db: AsyncSession, *, company_id: int, journal_entry
) -> None:
    if type(company_id) is not int or company_id <= 0:
        raise DirectCashJournalSourceError("Invalid company identity")
    if journal_entry.company_id != company_id:
        raise DirectCashJournalSourceError("Journal company mismatch")

    physical_accounts = set(
        (await db.scalars(
            select(CashDesk.accounting_account_id).where(
                CashDesk.company_id == company_id
            )
        )).all()
    )
    physical_accounts.update(
        (await db.scalars(
            select(Payment.cash_account_id_snapshot).where(
                Payment.company_id == company_id,
                Payment.cash_desk_id.is_not(None),
                Payment.cash_account_id_snapshot.is_not(None),
            )
        )).all()
    )
    physical_accounts.update(
        (await db.scalars(
            select(PayrollDisbursement.cash_account_id_snapshot).where(
                PayrollDisbursement.company_id == company_id,
                PayrollDisbursement.cash_desk_id.is_not(None),
                PayrollDisbursement.cash_account_id_snapshot.is_not(None),
            )
        )).all()
    )

    physical_lines = [
        line for line in journal_entry.lines
        if line.account_id in physical_accounts
    ]
    if not physical_lines:
        return

    if journal_entry.reversal_of_id is not None:
        raise DirectCashJournalSourceError(
            "Cash reversal requires original source lifecycle"
        )

    fields = [
        name for name in _SOURCE_FIELDS
        if getattr(journal_entry, name, None) is not None
    ]
    if len(fields) != 1 or fields[0] not in (
        "payment_id", "payroll_disbursement_id"
    ):
        raise DirectCashJournalSourceError(
            "Physical cash journal requires exactly one "
            "supported financial source"
        )

    kind = (
        "payment" if fields[0] == "payment_id" else "payroll"
    )
    source_id = getattr(journal_entry, fields[0])
    scope = db.info.get("_erp_cash_gl_lifecycle_scope")

    if (
        type(source_id) is not int
        or source_id <= 0
        or scope not in (
            (kind, source_id, "post"),
            (kind, source_id, "reverse"),
        )
    ):
        raise DirectCashJournalSourceError(
            "Physical cash GL requires its owning lifecycle"
        )

    if kind == "payment":
        from app.services.cash_payment_gl_evidence_service import (
            PaymentCashGLEvidenceError,
            validate_payment_cash_leg,
        )

        source = await db.scalar(
            select(Payment).where(
                Payment.company_id == company_id,
                Payment.id == source_id,
            )
        )
        if (
            source is None
            or source.cash_desk_id is None
            or source.bank_account_id is not None
            or source.cash_account_id_snapshot is None
            or str(getattr(source.status, "value", source.status))
            != "confirmed"
            or journal_entry.entry_date != source.payment_date
            or source.currency_code != "UAH"
        ):
            raise DirectCashJournalSourceError(
                "Cash Payment provenance mismatch"
            )
        expected_account = source.cash_account_id_snapshot
        try:
            validate_payment_cash_leg(
                lines=journal_entry.lines,
                cash_account_id=expected_account,
                amount=source.amount,
                direction=str(
                    getattr(source.direction, "value", source.direction)
                ),
            )
        except PaymentCashGLEvidenceError as exc:
            raise DirectCashJournalSourceError(str(exc)) from exc

    else:
        from app.services.cash_payroll_gl_evidence_service import (
            PayrollCashGLEvidenceError,
            validate_cash_money_leg,
        )

        source = await db.scalar(
            select(PayrollDisbursement).where(
                PayrollDisbursement.company_id == company_id,
                PayrollDisbursement.id == source_id,
            )
        )
        if (
            source is None
            or source.cash_desk_id is None
            or source.bank_account_id is not None
            or source.cash_account_id_snapshot is None
            or source.currency_code != "UAH"
            or journal_entry.entry_date != source.payment_date
        ):
            raise DirectCashJournalSourceError(
                "Cash Payroll provenance mismatch"
            )
        expected_account = source.cash_account_id_snapshot
        try:
            validate_cash_money_leg(
                lines=journal_entry.lines,
                cash_account_id=expected_account,
                amount=Decimal(source.amount),
            )
        except PayrollCashGLEvidenceError as exc:
            raise DirectCashJournalSourceError(str(exc)) from exc

    if (
        len(physical_lines) != 1
        or physical_lines[0].account_id != expected_account
    ):
        raise DirectCashJournalSourceError(
            "Extra or mismatched physical cash GL legs"
        )

    # Reject forged source-to-desk linkage even when the account
    # was historically remapped to another desk.
    desk = await db.scalar(
        select(CashDesk).where(
            CashDesk.company_id == company_id,
            CashDesk.id == source.cash_desk_id,
        )
    )
    if desk is None or desk.currency_code != source.currency_code:
        raise DirectCashJournalSourceError(
            "Physical cash desk/currency provenance invalid"
        )
