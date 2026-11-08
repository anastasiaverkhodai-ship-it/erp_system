"""Strict reconciliation of payment-specific tax source claims.

This guard checks internal consistency, NOT independent issuer
authenticity. No output from this module is statutory approval.
"""

from datetime import date
from decimal import Decimal, InvalidOperation

from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
)

COMPONENTS = {"personal_income_tax", "military_levy"}


def _id(value, label):
    if type(value) is not int or value <= 0:
        raise PaymentTaxAllocationError(
            f"Invalid {label} identity"
        )
    return value


def _amount(value):
    if type(value) is not str:
        raise PaymentTaxAllocationError(
            "Tax amount must be a decimal string"
        )
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise PaymentTaxAllocationError(
            "Invalid tax amount"
        ) from exc

    if (
        not number.is_finite()
        or number <= 0
        or number != number.quantize(Decimal("0.01"))
    ):
        raise PaymentTaxAllocationError(
            "Invalid positive monetary amount"
        )
    return number


def assess_payment_tax_claims(
    *,
    company_id,
    payments,
    claims,
):
    """Validate linkage without converting claims into proof.

    Payments: id, payment_date, active, currency_code.
    Claims: company_id, payment_id, component, amount,
            source_id, source_date, currency_code.
    """
    _id(company_id, "company")

    if type(payments) is not list or type(claims) is not list:
        raise PaymentTaxAllocationError(
            "Payment and claim collections required"
        )

    payment_index = {}

    for payment in payments:
        if type(payment) is not dict:
            raise PaymentTaxAllocationError(
                "Invalid payment record"
            )

        payment_id = _id(payment.get("id"), "payment")

        if payment_id in payment_index:
            raise PaymentTaxAllocationError(
                "Duplicate payment identity"
            )

        if (
            type(payment.get("payment_date")) is not date
            or type(payment.get("active")) is not bool
            or payment.get("currency_code") != "UAH"
        ):
            raise PaymentTaxAllocationError(
                "Invalid payment state"
            )

        payment_index[payment_id] = payment

    source_ids = set()
    claim_pairs = set()
    totals = {}
    normalized = []

    for claim in claims:
        if type(claim) is not dict:
            raise PaymentTaxAllocationError(
                "Invalid withholding claim"
            )

        if _id(claim.get("company_id"), "company") != company_id:
            raise PaymentTaxAllocationError(
                "Cross-company withholding claim"
            )

        payment_id = _id(
            claim.get("payment_id"), "payment"
        )
        source_id = _id(
            claim.get("source_id"), "source"
        )

        component = claim.get("component")

        if component not in COMPONENTS:
            raise PaymentTaxAllocationError(
                "Unsupported tax component"
            )

        if source_id in source_ids:
            raise PaymentTaxAllocationError(
                "Duplicate source reuse"
            )
        source_ids.add(source_id)

        pair = (payment_id, component)

        if pair in claim_pairs:
            raise PaymentTaxAllocationError(
                "Duplicate payment tax component"
            )
        claim_pairs.add(pair)

        payment = payment_index.get(payment_id)

        if payment is None:
            raise PaymentTaxAllocationError(
                "Payment source not found"
            )

        if not payment["active"]:
            raise PaymentTaxAllocationError(
                "Inactive payment source"
            )

        source_date = claim.get("source_date")

        if (
            type(source_date) is not date
            or source_date < payment["payment_date"]
            or claim.get("currency_code") != "UAH"
        ):
            raise PaymentTaxAllocationError(
                "Invalid source date or currency"
            )

        amount = _amount(claim.get("amount"))
        totals[pair] = amount

        normalized.append({
            "payment_id": payment_id,
            "component": component,
            "claimed_amount": str(amount),
            "source_id": source_id,
            "source_date": source_date.isoformat(),
            "source_authenticity": "NOT_VERIFIED",
            "actual_withholding": "NOT_VERIFIED",
        })

    return {
        "company_id": company_id,
        "payment_count": len(payment_index),
        "claim_count": len(normalized),
        "claims": normalized,
        "payment_specific_withholding": "NOT_VERIFIED",
        "independent_source_authenticity": "NOT_VERIFIED",
        "gross_paid_attribution": "NOT_APPROVED",
        "official_generation": "BLOCKED",
        "read_only": True,
    }
