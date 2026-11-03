"""Open a documented severance postpayment correction case.

Only creates an immutable review-stage record. No source revision,
payout, refund, payroll accrual or accounting journal is posted.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models.payroll import PayrollCalculation
from app.models.payroll_severance_correction_case import (
    PayrollSeveranceCorrectionCase,
)
from app.services.payroll_severance_postpayment_assessment import (
    assess_severance_postpayment,
)
from app.services.payroll_severance_postpayment_funding import (
    load_posted_payroll_funding,
)
from app.services.idempotency_fingerprint_service import (
    generate_request_fingerprint,
)


class SeveranceCorrectionCaseError(ValueError):
    pass


def validate_case_request(
    *,
    company_id,
    payroll_calculation_id,
    as_of,
    evidence_date,
    evidence_reference,
    request_key,
    actor_id,
):
    for name, value in (
        ("company_id", company_id),
        ("payroll_calculation_id", payroll_calculation_id),
        ("actor_id", actor_id),
    ):
        if type(value) is not int or value <= 0:
            raise SeveranceCorrectionCaseError(
                f"Invalid {name}"
            )

    if type(as_of) is not date or type(evidence_date) is not date:
        raise SeveranceCorrectionCaseError(
            "Documented dates required"
        )

    if evidence_date > as_of:
        raise SeveranceCorrectionCaseError(
            "Evidence date cannot be after assessment"
        )

    if (
        not isinstance(evidence_reference, str)
        or not 10 <= len(evidence_reference.strip()) <= 500
    ):
        raise SeveranceCorrectionCaseError(
            "Documented correction evidence required"
        )

    if (
        not isinstance(request_key, str)
        or not 8 <= len(request_key.strip()) <= 120
    ):
        raise SeveranceCorrectionCaseError(
            "Valid idempotency request key required"
        )

    return {
        "payroll_calculation_id": payroll_calculation_id,
        "as_of": as_of.isoformat(),
        "evidence_date": evidence_date.isoformat(),
        "evidence_reference": evidence_reference.strip(),
        "actor_id": actor_id,
    }


async def open_postpayment_severance_case(
    db,
    *,
    company_id,
    payroll_calculation_id,
    as_of,
    evidence_date,
    evidence_reference,
    request_key,
    actor_id,
):
    payload = validate_case_request(
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
        as_of=as_of,
        evidence_date=evidence_date,
        evidence_reference=evidence_reference,
        request_key=request_key,
        actor_id=actor_id,
    )

    key = request_key.strip()
    fingerprint = generate_request_fingerprint(payload)

    from app.models.company import Company

    # Serialize duplicate and competing case requests per company.
    locked = await db.scalar(
        select(Company.id).where(
            Company.id == company_id
        ).with_for_update()
    )

    if locked is None:
        raise SeveranceCorrectionCaseError(
            "Company not found"
        )

    existing = await db.scalar(
        select(PayrollSeveranceCorrectionCase).where(
            PayrollSeveranceCorrectionCase.company_id
            == company_id,
            PayrollSeveranceCorrectionCase.request_key == key,
        )
    )

    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise SeveranceCorrectionCaseError(
                "Request key already used with different evidence"
            )
        return existing

    calculation = await db.scalar(
        select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id == payroll_calculation_id,
        )
    )

    if calculation is None:
        raise SeveranceCorrectionCaseError(
            "Payroll calculation not found"
        )

    if calculation.currency_code != "UAH":
        raise SeveranceCorrectionCaseError(
            "Only UAH severance supported"
        )

    other_active = await db.scalar(
        select(PayrollSeveranceCorrectionCase.id).where(
            PayrollSeveranceCorrectionCase.company_id == company_id,
            PayrollSeveranceCorrectionCase.original_payroll_calculation_id
            == payroll_calculation_id,
            PayrollSeveranceCorrectionCase.status == "review_required",
        ).limit(1)
    )

    if other_active is not None:
        raise SeveranceCorrectionCaseError(
            "An active correction case already exists"
        )

    assessment = await assess_severance_postpayment(
        db,
        company_id=company_id,
        payroll_input_id=calculation.payroll_input_id,
    )

    if (
        assessment.last_calculated_source_id is None
        or assessment.original_gross <= Decimal("0.00")
        or assessment.posted_disbursement_count < 1
    ):
        raise SeveranceCorrectionCaseError(
            "Verified severance and a posted payment required"
        )

    funding = await load_posted_payroll_funding(
        db,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
        as_of=as_of,
    )

    if (
        funding.posted_payment_count
        != assessment.posted_disbursement_count
        or funding.posted_payment_total
        != assessment.posted_disbursement_total
        or funding.posted_payment_total <= Decimal("0.00")
    ):
        raise SeveranceCorrectionCaseError(
            "Dated posted payment evidence is incomplete"
        )

    row = PayrollSeveranceCorrectionCase(
        company_id=company_id,
        payroll_input_id=calculation.payroll_input_id,
        original_payroll_calculation_id=payroll_calculation_id,
        original_severance_source_id=(
            assessment.last_calculated_source_id
        ),
        request_key=key,
        request_fingerprint=fingerprint,
        evidence_reference=evidence_reference.strip(),
        evidence_date=evidence_date,
        original_severance_gross=assessment.original_gross,
        posted_payroll_amount_at_opening=(
            funding.posted_payment_total
        ),
        as_of_date=as_of,
        status="review_required",
        created_by=actor_id,
    )

    db.add(row)
    await db.flush()
    return row
