"""Persist payment-specific tax allocation CANDIDATES.

The persisted allocation is a review draft, not evidence
of actual payment-specific employee withholding.

No 4DF official generation or gross-paid approval.
"""

from decimal import Decimal, InvalidOperation

from sqlalchemy import select, text

from app.models.payroll import PayrollCalculation
from app.models.payroll_disbursement import PayrollDisbursement
from app.models.payroll_statutory import (
    PayrollStatutoryResult,
    PayrollStatutoryResultLine,
)
from app.services.payroll_regulatory_payment_evidence import (
    active_posted_journal_query,
)


TABLE = "payroll_payment_tax_allocation_candidates"
COMPONENTS = {"personal_income_tax", "military_levy"}


class PaymentTaxAllocationError(ValueError):
    pass


def require_id(value, label):
    if type(value) is not int or value <= 0:
        raise PaymentTaxAllocationError("Invalid " + label)
    return value


def require_text(value, label, max_length):
    if (
        type(value) is not str
        or not value.strip()
        or value != value.strip()
        or len(value) > max_length
    ):
        raise PaymentTaxAllocationError("Invalid " + label)
    return value


def money(value):
    if type(value) not in (str, Decimal, int):
        raise PaymentTaxAllocationError("Invalid amount type")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise PaymentTaxAllocationError("Invalid amount") from None
    if (
        not result.is_finite()
        or result <= 0
        or result != result.quantize(Decimal("0.01"))
    ):
        raise PaymentTaxAllocationError("Invalid monetary amount")
    return result


async def create_payment_tax_allocation_candidate(
    db,
    *,
    company_id,
    payroll_calculation_id,
    payroll_disbursement_id,
    statutory_line_id,
    amount,
    request_key,
    evidence_reference,
    created_by,
):
    company_id = require_id(company_id, "company")
    calc_id = require_id(
        payroll_calculation_id, "payroll calculation"
    )
    payment_id = require_id(
        payroll_disbursement_id, "payroll disbursement"
    )
    line_id = require_id(statutory_line_id, "statutory line")
    actor_id = require_id(created_by, "actor")
    value = money(amount)
    key = require_text(request_key, "request key", 150)
    evidence = require_text(
        evidence_reference, "evidence reference", 500
    )

    # The calculation is the serialization point for
    # concurrent allocations of all its partial payments.
    calculation = await db.scalar(
        select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id == calc_id,
        ).with_for_update()
    )
    if calculation is None:
        raise PaymentTaxAllocationError(
            "Payroll calculation not found"
        )
    if calculation.currency_code != "UAH":
        raise PaymentTaxAllocationError("UAH required")

    payment = await db.scalar(
        select(PayrollDisbursement).where(
            PayrollDisbursement.company_id == company_id,
            PayrollDisbursement.id == payment_id,
            PayrollDisbursement.payroll_calculation_id == calc_id,
        )
    )
    if (
        payment is None
        or payment.cancelled_at is not None
        or payment.reversed_on is not None
        or payment.currency_code != "UAH"
    ):
        raise PaymentTaxAllocationError(
            "Active source payment not found"
        )

    journal = await db.scalar(
        active_posted_journal_query(
            company_id=company_id,
            payroll_disbursement_id=payment_id,
        )
    )
    if journal is None:
        raise PaymentTaxAllocationError(
            "Posted payment journal required"
        )

    statutory = await db.scalar(
        select(PayrollStatutoryResult).where(
            PayrollStatutoryResult.company_id == company_id,
            PayrollStatutoryResult.payroll_calculation_id == calc_id,
        )
    )
    if statutory is None or statutory.currency_code != "UAH":
        raise PaymentTaxAllocationError(
            "Statutory calculation evidence required"
        )

    line = await db.scalar(
        select(PayrollStatutoryResultLine).where(
            PayrollStatutoryResultLine.company_id == company_id,
            PayrollStatutoryResultLine.id == line_id,
            PayrollStatutoryResultLine.payroll_statutory_result_id
                == statutory.id,
        )
    )
    if (
        line is None
        or line.component not in COMPONENTS
        or line.currency_code != "UAH"
    ):
        raise PaymentTaxAllocationError(
            "PIT/levy source line not found"
        )

    existing = (
        await db.execute(
            text(f"""
                SELECT id, payroll_calculation_id,
                       payroll_disbursement_id,
                       statutory_line_id, component,
                       amount, evidence_reference
                FROM {TABLE}
                WHERE company_id=:c AND request_key=:k
            """),
            {"c": company_id, "k": key},
        )
    ).mappings().first()

    if existing is not None:
        if (
            existing["payroll_calculation_id"] != calc_id
            or existing["payroll_disbursement_id"] != payment_id
            or existing["statutory_line_id"] != line_id
            or existing["component"] != line.component
            or Decimal(existing["amount"]) != value
            or existing["evidence_reference"] != evidence
        ):
            raise PaymentTaxAllocationError(
                "Idempotency key conflict"
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
                    "candidate": existing["id"],
                },
            )
        ).scalar_one_or_none()

        if revoked is not None:
            raise PaymentTaxAllocationError(
                "Revoked candidate cannot be reused"
            )

        return {
            "id": existing["id"],
            "status": "draft",
            "payment_specific_withholding_verified": False,
            "official_generation": "BLOCKED",
        }

    used = (
        await db.execute(
            text(f"""
                SELECT COALESCE(SUM(
                    COALESCE(latest.amount, c.amount)
                ), 0)
                FROM {TABLE} c
                LEFT JOIN LATERAL (
                    SELECT x.amount
                    FROM payroll_payment_tax_allocation_corrections x
                    WHERE x.company_id=c.company_id
                      AND x.original_candidate_id=c.id
                    ORDER BY x.id DESC
                    LIMIT 1
                ) latest ON TRUE
                WHERE c.company_id=:c
                  AND c.statutory_line_id=:l
            """),
            {"c": company_id, "l": line_id},
        )
    ).scalar_one()

    if Decimal(used) + value > Decimal(line.amount):
        raise PaymentTaxAllocationError(
            "Tax allocation exceeds accrued statutory line"
        )

    duplicate = (
        await db.execute(
            text(f"""
                SELECT id FROM {TABLE}
                WHERE company_id=:c
                  AND payroll_disbursement_id=:p
                  AND statutory_line_id=:l
            """),
            {"c": company_id, "p": payment_id, "l": line_id},
        )
    ).scalar_one_or_none()

    if duplicate is not None:
        raise PaymentTaxAllocationError(
            "Duplicate payment/source allocation"
        )

    result = await db.execute(
        text(f"""
            INSERT INTO {TABLE} (
                company_id, payroll_calculation_id,
                payroll_disbursement_id, statutory_line_id,
                component, amount, request_key,
                evidence_reference, created_by, status
            ) VALUES (
                :c, :calc, :payment, :line,
                :component, :amount, :key,
                :evidence, :actor, 'draft'
            )
            RETURNING id
        """),
        {
            "c": company_id,
            "calc": calc_id,
            "payment": payment_id,
            "line": line_id,
            "component": line.component,
            "amount": value,
            "key": key,
            "evidence": evidence,
            "actor": actor_id,
        },
    )

    return {
        "id": result.scalar_one(),
        "status": "draft",
        "source_component": line.component,
        "source_statutory_line_id": line_id,
        "source_payment_id": payment_id,
        "candidate_amount": str(value),
        "payment_specific_withholding_verified": False,
        "gross_paid_approved": False,
        "official_generation": "BLOCKED",
    }
