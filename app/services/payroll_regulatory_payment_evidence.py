"""Verify actual payroll and tax payment records for 4DF drafts.

This adapter validates posted source identity and amounts.
It does not infer the official budget receipt date.
"""
from decimal import Decimal
from sqlalchemy import select, exists

from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)

from app.models.payroll_disbursement import PayrollDisbursement
from app.models.payroll_tax_remittance import (
    PayrollTaxRemittance,
    PayrollTaxRemittanceLine,
)
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)


def decimal_amount(value):
    try:
        if isinstance(value, bool) or value is None:
            raise ValueError
        number = Decimal(str(value))
    except (ValueError, TypeError, ArithmeticError):
        raise PayrollRegulatoryReportError(
            "Invalid payment evidence amount"
        )

    if not number.is_finite() or number < 0:
        raise PayrollRegulatoryReportError(
            "Invalid payment evidence amount"
        )

    return number


def required_ids(values, label):
    if not isinstance(values, list):
        raise PayrollRegulatoryReportError(
            f"{label} IDs must be a list"
        )

    if not values:
        raise PayrollRegulatoryReportError(
            f"{label} evidence required"
        )

    if any(type(value) is not int or value <= 0 for value in values):
        raise PayrollRegulatoryReportError(
            f"Invalid {label} identifier"
        )

    if len(values) != len(set(values)):
        raise PayrollRegulatoryReportError(
            f"Duplicate {label} identifier"
        )

    return sorted(values)



def active_posted_journal_query(
    *,
    company_id: int,
    journal_entry_id: int | None = None,
    payroll_disbursement_id: int | None = None,
):
    """Return only original posted journals without posted reversals."""
    if (journal_entry_id is None) == (
        payroll_disbursement_id is None
    ):
        raise PayrollRegulatoryReportError(
            "Exactly one journal source is required"
        )

    if type(company_id) is not int or company_id <= 0:
        raise PayrollRegulatoryReportError(
            "Valid company required"
        )

    from sqlalchemy.orm import aliased

    reversal = aliased(JournalEntry)

    query = select(JournalEntry.id).where(
        JournalEntry.company_id == company_id,
        JournalEntry.status == JournalEntryStatus.POSTED,
        JournalEntry.reversal_of_id.is_(None),
        ~exists(
            select(reversal.id).where(
                reversal.company_id == company_id,
                reversal.reversal_of_id == JournalEntry.id,
                reversal.status == JournalEntryStatus.POSTED,
            )
        ),
    )

    if journal_entry_id is not None:
        if type(journal_entry_id) is not int or journal_entry_id <= 0:
            raise PayrollRegulatoryReportError(
                "Invalid journal identifier"
            )
        query = query.where(JournalEntry.id == journal_entry_id)

    if payroll_disbursement_id is not None:
        if (
            type(payroll_disbursement_id) is not int
            or payroll_disbursement_id <= 0
        ):
            raise PayrollRegulatoryReportError(
                "Invalid payroll disbursement identifier"
            )
        query = query.where(
            JournalEntry.payroll_disbursement_id
            == payroll_disbursement_id
        )

    return query


async def verify_4df_posted_payment_evidence(
    db,
    *,
    company_id,
    reviewed_allocations,
):
    if not isinstance(reviewed_allocations, (tuple, list)):
        raise PayrollRegulatoryReportError(
            "Reviewed allocation evidence required"
        )

    seen_payment_ids = set()
    seen_remittance_lines = set()

    for allocation in reviewed_allocations:
        if not isinstance(allocation, dict):
            raise PayrollRegulatoryReportError(
                "Invalid 4DF allocation"
            )

        calculation_id = allocation.get(
            "payroll_calculation_id"
        )

        if type(calculation_id) is not int:
            raise PayrollRegulatoryReportError(
                "Payroll calculation identifier required"
            )

        payment_ids = required_ids(
            allocation.get("payroll_disbursement_ids"),
            "Payroll payment",
        )

        tax_line_ids = required_ids(
            allocation.get("tax_remittance_line_ids"),
            "Tax remittance line",
        )

        if seen_payment_ids.intersection(payment_ids):
            raise PayrollRegulatoryReportError(
                "Payroll payment allocated more than once"
            )

        if seen_remittance_lines.intersection(tax_line_ids):
            raise PayrollRegulatoryReportError(
                "Tax remittance line allocated more than once"
            )

        seen_payment_ids.update(payment_ids)
        seen_remittance_lines.update(tax_line_ids)

        payments = list((
            await db.scalars(
                select(PayrollDisbursement)
                .where(
                    PayrollDisbursement.company_id == company_id,
                    PayrollDisbursement.id.in_(payment_ids),
                    PayrollDisbursement.payroll_calculation_id
                        == calculation_id,
                )
            )
        ).all())

        if len(payments) != len(payment_ids):
            raise PayrollRegulatoryReportError(
                "Payroll payment evidence missing or cross-company"
            )

        total_paid = Decimal("0.00")
        payment_dates = set()

        for payment in payments:
            if (
                payment.cancelled_at is not None
                or payment.reversed_on is not None
                or payment.currency_code != "UAH"
            ):
                raise PayrollRegulatoryReportError(
                    "Cancelled, reversed or invalid-currency payroll payment"
                )

            posted_payment = await db.scalar(
                active_posted_journal_query(
                    company_id=company_id,
                    payroll_disbursement_id=payment.id,
                )
            )

            if posted_payment is None:
                raise PayrollRegulatoryReportError(
                    "Payroll disbursement has no active posted journal"
                )

            expected_employee_id = allocation.get(
                "_verified_employee_id"
            )

            if (
                type(expected_employee_id) is not int
                or expected_employee_id <= 0
            ):
                raise PayrollRegulatoryReportError(
                    "Verified payroll employee evidence required"
                )

            if payment.employee_id != expected_employee_id:
                raise PayrollRegulatoryReportError(
                    "Payroll payment employee mismatch"
                )

            total_paid += decimal_amount(payment.amount)
            payment_dates.add(payment.payment_date)

        if total_paid != decimal_amount(
            allocation.get("paid_amount")
        ):
            raise PayrollRegulatoryReportError(
                "4DF payment amount differs from posted payroll payments"
            )

        if len(payment_dates) != 1:
            raise PayrollRegulatoryReportError(
                "4DF payments across multiple dates require split rows"
            )

        if allocation.get("payment_date") not in payment_dates:
            raise PayrollRegulatoryReportError(
                "4DF payment date differs from posted payroll payment"
            )

        tax_results = (
            await db.execute(
                select(
                    PayrollTaxRemittanceLine,
                    PayrollTaxRemittance,
                )
                .join(
                    PayrollTaxRemittance,
                    (
                        PayrollTaxRemittance.company_id
                        == PayrollTaxRemittanceLine.company_id
                    ) & (
                        PayrollTaxRemittance.id
                        == PayrollTaxRemittanceLine.remittance_id
                    ),
                )
                .where(
                    PayrollTaxRemittanceLine.company_id
                        == company_id,
                    PayrollTaxRemittanceLine.id.in_(
                        tax_line_ids
                    ),
                    PayrollTaxRemittanceLine.payroll_calculation_id
                        == calculation_id,
                )
            )
        ).all()

        if len(tax_results) != len(tax_line_ids):
            raise PayrollRegulatoryReportError(
                "Tax remittance evidence missing or cross-company"
            )

        tax_totals = {
            "personal_income_tax": Decimal("0.00"),
            "military_levy": Decimal("0.00"),
        }

        for line, remittance in tax_results:
            if remittance.component not in tax_totals:
                raise PayrollRegulatoryReportError(
                    "4DF remittance contains unsupported tax component"
                )

            if (
                not remittance.document_reference
                or not remittance.document_reference.strip()
                or not remittance.budget_payment_reference
                or not remittance.budget_payment_reference.strip()
                or remittance.journal_entry_id is None
            ):
                raise PayrollRegulatoryReportError(
                    "Tax remittance is missing payment evidence"
                )

            posted_tax_journal = await db.scalar(
                active_posted_journal_query(
                    company_id=company_id,
                    journal_entry_id=remittance.journal_entry_id,
                )
            )

            if posted_tax_journal is None:
                raise PayrollRegulatoryReportError(
                    "Tax remittance journal is not actively posted"
                )

            tax_totals[remittance.component] += decimal_amount(
                line.amount
            )

        if tax_totals["personal_income_tax"] != decimal_amount(
            allocation.get("pit_remitted")
        ):
            raise PayrollRegulatoryReportError(
                "4DF PIT remittance does not match posted allocation"
            )

        if tax_totals["military_levy"] != decimal_amount(
            allocation.get("levy_remitted")
        ):
            raise PayrollRegulatoryReportError(
                "4DF military levy remittance does not match posted allocation"
            )

        # These identifiers must be persisted in the immutable row
        # payload so the evidence remains traceable.
        allocation["payment_evidence_reference"] = (
            "payroll_disbursements:"
            + ",".join(map(str, payment_ids))
        )

        allocation["tax_remittance_evidence_reference"] = (
            "payroll_tax_remittance_lines:"
            + ",".join(map(str, tax_line_ids))
        )
