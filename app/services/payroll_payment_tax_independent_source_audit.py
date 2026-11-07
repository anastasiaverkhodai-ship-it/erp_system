"""Independent read-only inventory for payroll tax source evidence.

An accrual, payroll payment or budget remittance is not,
by itself, proof of PIT/levy withheld at one specific payout.
No statutory generation is authorized here.
"""

from decimal import Decimal

from sqlalchemy import text

from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
    require_id,
)
from app.services.payroll_regulatory_payment_evidence import (
    active_posted_journal_query,
)


def classify_sources(
    *,
    payroll_posted,
    payroll_confirmed,
    bank_matches,
    cash_documents,
    tax_remittance_components,
    independent_review_count,
):
    if (
        type(payroll_posted) is not bool
        or type(payroll_confirmed) is not bool
        or type(bank_matches) is not int
        or type(cash_documents) is not int
        or type(independent_review_count) is not int
        or min(
            bank_matches,
            cash_documents,
            independent_review_count,
        ) < 0
        or type(tax_remittance_components) is not dict
        or set(tax_remittance_components) != {
            "personal_income_tax",
            "military_levy",
        }
    ):
        raise PaymentTaxAllocationError(
            "Invalid independent source evidence"
        )

    values = {}

    for component, amount in tax_remittance_components.items():
        if (
            type(amount) is not Decimal
            or not amount.is_finite()
            or amount < 0
            or amount != amount.quantize(Decimal("0.01"))
        ):
            raise PaymentTaxAllocationError(
                f"Invalid {component} remittance total"
            )
        values[component] = str(amount)

    if not payroll_posted or not payroll_confirmed:
        payroll_status = "NOT_CONFIRMED_AND_POSTED"
    elif bank_matches and cash_documents:
        payroll_status = "CONFLICTING_BANK_AND_CASH_SOURCES"
    elif bank_matches == 1:
        payroll_status = "BANK_RECONCILIATION_PRESENT"
    elif cash_documents == 1:
        payroll_status = "CASH_DOCUMENT_PRESENT"
    elif bank_matches > 1 or cash_documents > 1:
        payroll_status = "AMBIGUOUS_PAYMENT_SOURCE"
    else:
        payroll_status = "POSTED_LEDGER_ONLY"

    return {
        "payroll_source_status": payroll_status,
        "bank_active_match_count": bank_matches,
        "cash_source_document_count": cash_documents,
        "tax_remittance_by_calculation": values,
        "tax_remittance_scope": "CALCULATION_NOT_PAYMENT",
        "independent_document_review_present": (
            independent_review_count > 0
        ),
        "document_source_authenticity": "NOT_VERIFIED",
        "payment_specific_pit_withheld": "NOT_VERIFIED",
        "payment_specific_levy_withheld": "NOT_VERIFIED",
        "payment_specific_withholding_verified": False,
        "gross_paid_attribution": "NOT_APPROVED",
        "official_generation": "BLOCKED",
    }


async def audit_payment_tax_sources(
    db,
    *,
    company_id,
    evidence_id,
):
    company_id = require_id(company_id, "company")
    evidence_id = require_id(evidence_id, "evidence")

    record = (
        await db.execute(
            text("""
                SELECT e.id,
                       e.company_id,
                       e.payroll_disbursement_id,
                       e.statutory_line_id,
                       e.candidate_id,
                       e.correction_id,
                       e.amount AS evidence_amount,
                       c.payroll_calculation_id,
                       p.amount AS payroll_amount,
                       p.confirmed_at,
                       p.cancelled_at,
                       p.reversed_on,
                       p.currency_code
                FROM payroll_payment_tax_evidence e
                JOIN payroll_payment_tax_allocation_candidates c
                  ON c.company_id=e.company_id
                 AND c.id=e.candidate_id
                JOIN payroll_disbursements p
                  ON p.company_id=e.company_id
                 AND p.id=e.payroll_disbursement_id
                 AND p.payroll_calculation_id=
                     c.payroll_calculation_id
                WHERE e.company_id=:company
                  AND e.id=:evidence
            """),
            {
                "company": company_id,
                "evidence": evidence_id,
            },
        )
    ).mappings().first()

    if record is None:
        raise PaymentTaxAllocationError(
            "Payment tax source not found for company"
        )

    if (
        record["cancelled_at"] is not None
        or record["reversed_on"] is not None
        or record["currency_code"] != "UAH"
    ):
        raise PaymentTaxAllocationError(
            "Inactive or invalid-currency payroll source"
        )

    latest_correction = await db.scalar(
        text("""
            SELECT id
            FROM payroll_payment_tax_allocation_corrections
            WHERE company_id=:company
              AND original_candidate_id=:candidate
            ORDER BY id DESC
            LIMIT 1
        """),
        {
            "company": company_id,
            "candidate": record["candidate_id"],
        },
    )

    if record["correction_id"] != latest_correction:
        raise PaymentTaxAllocationError(
            "Stale payment tax evidence correction"
        )

    revoked = await db.scalar(
        text("""
            SELECT 1
            FROM payroll_payment_tax_allocation_candidate_events
            WHERE company_id=:company
              AND candidate_id=:candidate
              AND event_kind='revoked'
            LIMIT 1
        """),
        {
            "company": company_id,
            "candidate": record["candidate_id"],
        },
    )

    if revoked is not None and latest_correction is None:
        raise PaymentTaxAllocationError(
            "Revoked allocation without current replacement"
        )

    posted = await db.scalar(
        active_posted_journal_query(
            company_id=company_id,
            payroll_disbursement_id=record[
                "payroll_disbursement_id"
            ],
        )
    )

    bank_matches = await db.scalar(
        text("""
            SELECT COUNT(*)
            FROM bank_statement_reconciliation_active_links a
            JOIN bank_statement_reconciliations r
              ON r.company_id=a.company_id
             AND r.id=a.reconciliation_id
            JOIN bank_statement_lines b
              ON b.company_id=a.company_id
             AND b.id=a.bank_statement_line_id
            WHERE a.company_id=:company
              AND a.payroll_disbursement_id=:payment
              AND r.payroll_disbursement_id=:payment
              AND r.reversal_of_id IS NULL
              AND r.matched_amount=:amount
              AND r.currency_code='UAH'
              AND b.currency_code='UAH'
        """),
        {
            "company": company_id,
            "payment": record["payroll_disbursement_id"],
            "amount": record["payroll_amount"],
        },
    )

    cash_documents = await db.scalar(
        text("""
            SELECT COUNT(*)
            FROM payroll_cash_documents c
            WHERE c.company_id=:company
              AND c.payroll_disbursement_id=:payment
              AND c.reversal_of_id IS NULL
              AND c.amount=:amount
              AND c.currency_code='UAH'
        """),
        {
            "company": company_id,
            "payment": record["payroll_disbursement_id"],
            "amount": record["payroll_amount"],
        },
    )

    remittances = (
        await db.execute(
            text("""
                SELECT t.component,
                       t.journal_entry_id,
                       l.amount
                FROM payroll_tax_remittance_lines l
                JOIN payroll_tax_remittance t
                  ON t.company_id=l.company_id
                 AND t.id=l.remittance_id
                WHERE l.company_id=:company
                  AND l.payroll_calculation_id=:calculation
                  AND t.component IN (
                      'personal_income_tax',
                      'military_levy'
                  )
            """),
            {
                "company": company_id,
                "calculation": record["payroll_calculation_id"],
            },
        )
    ).mappings().all()

    totals = {
        "personal_income_tax": Decimal("0.00"),
        "military_levy": Decimal("0.00"),
    }

    for item in remittances:
        active = await db.scalar(
            active_posted_journal_query(
                company_id=company_id,
                journal_entry_id=item["journal_entry_id"],
            )
        )

        if active is None:
            continue

        amount = Decimal(item["amount"])

        if not amount.is_finite() or amount <= 0:
            raise PaymentTaxAllocationError(
                "Invalid remittance source amount"
            )

        totals[item["component"]] += amount

    review_count = await db.scalar(
        text("""
            SELECT COUNT(*)
            FROM payroll_payment_tax_document_reviews
            WHERE company_id=:company
              AND evidence_id=:evidence
        """),
        {
            "company": company_id,
            "evidence": evidence_id,
        },
    )

    result = classify_sources(
        payroll_posted=posted is not None,
        payroll_confirmed=record["confirmed_at"] is not None,
        bank_matches=bank_matches,
        cash_documents=cash_documents,
        tax_remittance_components=totals,
        independent_review_count=review_count,
    )

    return {
        "company_id": company_id,
        "evidence_id": evidence_id,
        "payroll_disbursement_id": record[
            "payroll_disbursement_id"
        ],
        "payroll_calculation_id": record[
            "payroll_calculation_id"
        ],
        "source_inventory": result,
        "independent_actual_withholding":
            "NOT_VERIFIED",
        "read_only": True,
        "official_generation": "BLOCKED",
    }
