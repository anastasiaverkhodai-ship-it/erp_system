"""Append-only source-bound corrections of draft tax candidates.

These are internal allocation corrections, not independently
verified tax withholding or approved statutory gross paid.
"""

from sqlalchemy import select, text

from app.models.payroll import PayrollCalculation
from app.models.payroll_disbursement import PayrollDisbursement
from app.models.payroll_statutory import PayrollStatutoryResultLine
from app.services.payroll_regulatory_payment_evidence import (
    active_posted_journal_query,
)
from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
    money,
    require_id,
    require_text,
)


TABLE = "payroll_payment_tax_allocation_corrections"
CANDIDATES = "payroll_payment_tax_allocation_candidates"


def validate_transition(last_id, previous_id):
    if last_id is None:
        if previous_id is not None:
            raise PaymentTaxAllocationError(
                "First correction cannot have predecessor"
            )
    elif previous_id != last_id:
        raise PaymentTaxAllocationError(
            "Stale or invalid correction predecessor"
        )


async def effective_source_total(
    db, *, company_id, statutory_line_id,
):
    return (
        await db.execute(
            text("""
                SELECT COALESCE(SUM(
                    COALESCE(latest.amount, c.amount)
                ), 0)
                FROM payroll_payment_tax_allocation_candidates c
                LEFT JOIN LATERAL (
                    SELECT x.amount
                    FROM payroll_payment_tax_allocation_corrections x
                    WHERE x.company_id=c.company_id
                      AND x.original_candidate_id=c.id
                    ORDER BY x.id DESC
                    LIMIT 1
                ) latest ON TRUE
                WHERE c.company_id=:company
                  AND c.statutory_line_id=:line
            """),
            {
                "company": company_id,
                "line": statutory_line_id,
            },
        )
    ).scalar_one()


async def correct_payment_tax_allocation(
    db,
    *,
    company_id,
    original_candidate_id,
    previous_correction_id,
    amount,
    request_key,
    evidence_reference,
    created_by,
):
    company_id = require_id(company_id, "company")
    original_candidate_id = require_id(
        original_candidate_id, "original candidate"
    )

    if previous_correction_id is not None:
        previous_correction_id = require_id(
            previous_correction_id, "previous correction"
        )

    created_by = require_id(created_by, "actor")
    amount = money(amount)
    request_key = require_text(
        request_key, "request key", 150
    )
    evidence_reference = require_text(
        evidence_reference, "evidence reference", 500
    )

    original = (
        await db.execute(
            text(f"""
                SELECT id, company_id,
                       payroll_calculation_id,
                       payroll_disbursement_id,
                       statutory_line_id, amount
                FROM {CANDIDATES}
                WHERE company_id=:company
                  AND id=:candidate
            """),
            {
                "company": company_id,
                "candidate": original_candidate_id,
            },
        )
    ).mappings().first()

    if original is None:
        raise PaymentTaxAllocationError(
            "Original candidate not found"
        )

    # Every correction on the calculation serializes against
    # original draft allocation creation and other corrections.
    calc = await db.scalar(
        select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id
                == original["payroll_calculation_id"],
        ).with_for_update()
    )

    if calc is None or calc.currency_code != "UAH":
        raise PaymentTaxAllocationError(
            "Source calculation unavailable"
        )

    payment = await db.scalar(
        select(PayrollDisbursement).where(
            PayrollDisbursement.company_id == company_id,
            PayrollDisbursement.id
                == original["payroll_disbursement_id"],
            PayrollDisbursement.payroll_calculation_id
                == original["payroll_calculation_id"],
        )
    )

    if (
        payment is None
        or payment.cancelled_at is not None
        or payment.reversed_on is not None
        or payment.currency_code != "UAH"
    ):
        raise PaymentTaxAllocationError(
            "Active source payment required"
        )

    journal = await db.scalar(
        active_posted_journal_query(
            company_id=company_id,
            payroll_disbursement_id
                =original["payroll_disbursement_id"],
        )
    )
    if journal is None:
        raise PaymentTaxAllocationError(
            "Posted payment journal required"
        )

    line = await db.scalar(
        select(PayrollStatutoryResultLine).where(
            PayrollStatutoryResultLine.company_id == company_id,
            PayrollStatutoryResultLine.id
                == original["statutory_line_id"],
        )
    )

    if (
        line is None
        or line.component not in (
            "personal_income_tax", "military_levy"
        )
        or line.currency_code != "UAH"
    ):
        raise PaymentTaxAllocationError(
            "Original statutory source unavailable"
        )

    revoked = (
        await db.execute(
            text("""
                SELECT 1
                FROM payroll_payment_tax_allocation_candidate_events
                WHERE company_id=:company
                  AND candidate_id=:candidate
                  AND event_kind='revoked'
            """),
            {
                "company": company_id,
                "candidate": original_candidate_id,
            },
        )
    ).scalar_one_or_none()

    if revoked is None:
        raise PaymentTaxAllocationError(
            "Original candidate must be revoked"
        )

    previous_request = (
        await db.execute(
            text(f"""
                SELECT id, original_candidate_id,
                       previous_correction_id,
                       amount, evidence_reference, created_by
                FROM {TABLE}
                WHERE company_id=:company
                  AND request_key=:key
            """),
            {
                "company": company_id,
                "key": request_key,
            },
        )
    ).mappings().first()

    if previous_request is not None:
        if (
            previous_request["original_candidate_id"]
                != original_candidate_id
            or previous_request["previous_correction_id"]
                != previous_correction_id
            or previous_request["amount"] != amount
            or previous_request["evidence_reference"]
                != evidence_reference
            or previous_request["created_by"] != created_by
        ):
            raise PaymentTaxAllocationError(
                "Correction idempotency conflict"
            )
        return {
            "correction_id": previous_request["id"],
            "idempotent": True,
            "payment_specific_withholding_verified": False,
            "official_generation": "BLOCKED",
        }

    latest = (
        await db.execute(
            text(f"""
                SELECT id, amount
                FROM {TABLE}
                WHERE company_id=:company
                  AND original_candidate_id=:original
                ORDER BY id DESC
                LIMIT 1
            """),
            {
                "company": company_id,
                "original": original_candidate_id,
            },
        )
    ).mappings().first()

    validate_transition(
        None if latest is None else latest["id"],
        previous_correction_id,
    )

    currently_used = await effective_source_total(
        db,
        company_id=company_id,
        statutory_line_id=original["statutory_line_id"],
    )

    old_effective = (
        original["amount"]
        if latest is None else latest["amount"]
    )

    new_total = currently_used - old_effective + amount

    if new_total > line.amount:
        raise PaymentTaxAllocationError(
            "Corrected allocation exceeds statutory source"
        )

    result = await db.execute(
        text(f"""
            INSERT INTO {TABLE} (
                company_id, original_candidate_id,
                previous_correction_id, amount,
                request_key, evidence_reference, created_by
            ) VALUES (
                :company, :original, :previous, :amount,
                :key, :evidence, :actor
            )
            RETURNING id
        """),
        {
            "company": company_id,
            "original": original_candidate_id,
            "previous": previous_correction_id,
            "amount": amount,
            "key": request_key,
            "evidence": evidence_reference,
            "actor": created_by,
        },
    )

    return {
        "correction_id": result.scalar_one(),
        "idempotent": False,
        "original_candidate_id": original_candidate_id,
        "effective_amount": str(amount),
        "payment_specific_withholding_verified": False,
        "gross_paid_approved": False,
        "official_generation": "BLOCKED",
    }
