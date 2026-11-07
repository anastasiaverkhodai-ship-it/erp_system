"""Source-bound payroll tax and payment reconciliation.

Accrued statutory PIT/levy, payroll net, disbursement,
non-statutory deductions and tax remittances are distinct.

This read-only review never infers payment-specific PIT/levy.
"""

import hashlib
import json
from decimal import Decimal

from sqlalchemy import select

from app.models.payroll import PayrollCalculation
from app.models.payroll_disbursement import PayrollDisbursement
from app.models.payroll_statutory import (
    PayrollStatutoryResult,
    PayrollStatutoryResultLine,
)
from app.services.payroll_regulatory_payment_evidence import (
    active_posted_journal_query,
)


SCHEMA = "ERP_WITHHOLDING_SOURCE_RECONCILIATION_V1"
CENT = Decimal("0.01")


class WithholdingReconciliationError(ValueError):
    pass


def require_id(value, name):
    if type(value) is not int or value <= 0:
        raise WithholdingReconciliationError(
            "Invalid " + name
        )
    return value


def money(value, name):
    if isinstance(value, (float, bool)) or not isinstance(
        value, (Decimal, int, str)
    ):
        raise WithholdingReconciliationError(
            "Invalid amount type: " + name
        )

    try:
        result = Decimal(str(value))
    except Exception:
        raise WithholdingReconciliationError(
            "Invalid monetary value: " + name
        ) from None

    if (
        not result.is_finite()
        or result < 0
        or result != result.quantize(CENT)
    ):
        raise WithholdingReconciliationError(
            "Invalid monetary amount: " + name
        )

    return result


def canonical(data):
    return json.dumps(
        data,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(data):
    return hashlib.sha256(
        canonical(data).encode("utf-8")
    ).hexdigest()


def reconcile_sources(
    *,
    gross,
    statutory_net,
    statutory_withholding,
    pit_accrued,
    levy_accrued,
    posted_payments,
):
    """Check source math without attributing accrued tax to payments."""

    gross = money(gross, "gross")
    net = money(statutory_net, "net")
    withheld = money(
        statutory_withholding, "statutory withholding"
    )
    pit = money(pit_accrued, "PIT")
    levy = money(levy_accrued, "military levy")

    if gross != net + withheld:
        raise WithholdingReconciliationError(
            "Statutory gross/net reconciliation failed"
        )

    if withheld != pit + levy:
        raise WithholdingReconciliationError(
            "PIT/levy components do not reconcile"
        )

    if type(posted_payments) is not list:
        raise WithholdingReconciliationError(
            "Posted payment inventory required"
        )

    ids = set()
    dates = set()
    total_paid = Decimal("0.00")
    other_withheld = Decimal("0.00")
    rows = []

    for item in posted_payments:
        if type(item) is not dict:
            raise WithholdingReconciliationError(
                "Invalid payment"
            )

        identifier = require_id(
            item.get("id"), "payment ID"
        )

        if identifier in ids:
            raise WithholdingReconciliationError(
                "Duplicate payment ID"
            )

        ids.add(identifier)

        paid = money(item.get("amount"), "paid")
        non_tax = money(
            item.get("non_tax_withheld"),
            "non-tax withholding",
        )

        if paid <= 0:
            raise WithholdingReconciliationError(
                "Payment must be positive"
            )

        day = item.get("payment_date")

        from datetime import date
        if type(day) is not date:
            raise WithholdingReconciliationError(
                "Verified payment date required"
            )

        dates.add(day.isoformat())
        total_paid += paid
        other_withheld += non_tax

        rows.append({
            "payment_id": identifier,
            "payment_date": day.isoformat(),
            "net_disbursed": str(paid),
            "non_tax_withheld": str(non_tax),
            "pit_at_this_payment": "NOT_VERIFIED",
            "levy_at_this_payment": "NOT_VERIFIED",
        })

    rows.sort(key=lambda row: row["payment_id"])

    if total_paid + other_withheld > net:
        raise WithholdingReconciliationError(
            "Disbursements exceed statutory net"
        )

    remaining = net - total_paid - other_withheld

    return {
        "gross_accrued": str(gross),
        "statutory_net": str(net),
        "pit_accrued": str(pit),
        "levy_accrued": str(levy),
        "posted_cash_paid": str(total_paid),
        "non_tax_withheld": str(other_withheld),
        "remaining_net_before_other_settlements": str(
            remaining
        ),
        "posted_payment_count": len(rows),
        "posted_payment_dates": sorted(dates),
        "payments": rows,
        "statutory_accrual_reconciliation": "PASS",
        "payment_specific_withholding":
            "NOT_VERIFIED",
        "gross_paid_attribution": "NOT_APPROVED",
        "official_generation": "BLOCKED",
    }


async def build_source_reconciliation(
    db,
    *,
    company_id,
    payroll_calculation_id,
):
    """Load real payroll sources and active posted disbursements."""

    require_id(company_id, "company")
    require_id(
        payroll_calculation_id, "payroll calculation"
    )

    calculation = await db.scalar(
        select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id == payroll_calculation_id,
        )
    )

    if calculation is None:
        raise WithholdingReconciliationError(
            "Payroll calculation not found for company"
        )

    statutory = await db.scalar(
        select(PayrollStatutoryResult).where(
            PayrollStatutoryResult.company_id == company_id,
            PayrollStatutoryResult.payroll_calculation_id
            == payroll_calculation_id,
        )
    )

    if statutory is None:
        raise WithholdingReconciliationError(
            "Persisted statutory result missing"
        )

    if (
        calculation.currency_code != "UAH"
        or statutory.currency_code != "UAH"
        or statutory.gross_amount != calculation.gross_amount
    ):
        raise WithholdingReconciliationError(
            "Currency or calculation gross mismatch"
        )

    lines = list((
        await db.scalars(
            select(PayrollStatutoryResultLine).where(
                PayrollStatutoryResultLine.company_id
                == company_id,
                PayrollStatutoryResultLine.payroll_statutory_result_id
                == statutory.id,
            )
        )
    ).all())

    if not lines:
        raise WithholdingReconciliationError(
            "Statutory component lines missing"
        )

    totals = {
        "personal_income_tax": Decimal("0.00"),
        "military_levy": Decimal("0.00"),
        "unified_social_contribution": Decimal("0.00"),
    }

    for line in lines:
        if line.component not in totals:
            raise WithholdingReconciliationError(
                "Unsupported statutory component"
            )

        if line.currency_code != "UAH":
            raise WithholdingReconciliationError(
                "Statutory component currency mismatch"
            )

        totals[line.component] += money(
            line.amount, "statutory component"
        )

    payments = list((
        await db.scalars(
            select(PayrollDisbursement).where(
                PayrollDisbursement.company_id == company_id,
                PayrollDisbursement.payroll_calculation_id
                == payroll_calculation_id,
            ).order_by(PayrollDisbursement.id)
        )
    ).all())

    posted = []
    excluded = []

    for payment in payments:
        if (
            payment.cancelled_at is not None
            or payment.reversed_on is not None
        ):
            excluded.append(payment.id)
            continue

        verified_journal = await db.scalar(
            active_posted_journal_query(
                company_id=company_id,
                payroll_disbursement_id=payment.id,
            )
        )

        if verified_journal is None:
            excluded.append(payment.id)
            continue

        if (
            payment.currency_code != "UAH"
            or payment.confirmed_at is None
        ):
            raise WithholdingReconciliationError(
                "Invalid confirmed payment source"
            )

        posted.append({
            "id": payment.id,
            "payment_date": payment.payment_date,
            "amount": payment.amount,
            "non_tax_withheld": payment.withheld_amount,
        })

    review = reconcile_sources(
        gross=statutory.gross_amount,
        statutory_net=statutory.net_amount,
        statutory_withholding=statutory.employee_withholding_amount,
        pit_accrued=totals["personal_income_tax"],
        levy_accrued=totals["military_levy"],
        posted_payments=posted,
    )

    if (
        totals["unified_social_contribution"]
        != money(
            statutory.employer_contribution_amount,
            "employer contribution",
        )
    ):
        raise WithholdingReconciliationError(
            "USC employer contribution mismatch"
        )

    body = {
        "schema": SCHEMA,
        "company_id": company_id,
        "payroll_calculation_id": payroll_calculation_id,
        "payroll_period_id": calculation.payroll_period_id,
        "statutory_result_id": statutory.id,
        "statutory_line_ids": sorted(
            line.id for line in lines
        ),
        "excluded_payment_ids": excluded,
        "reconciliation": review,
        "read_only": True,
        "official_generation": "BLOCKED",
    }

    return {
        "body": body,
        "sha256": digest(body),
    }


def verify_source_reconciliation(document):
    if (
        type(document) is not dict
        or set(document) != {"body", "sha256"}
        or type(document["body"]) is not dict
    ):
        raise WithholdingReconciliationError(
            "Invalid source reconciliation document"
        )

    body = document["body"]
    review = body.get("reconciliation")

    if (
        body.get("schema") != SCHEMA
        or body.get("read_only") is not True
        or body.get("official_generation") != "BLOCKED"
        or type(review) is not dict
        or review.get("payment_specific_withholding")
            != "NOT_VERIFIED"
        or review.get("gross_paid_attribution")
            != "NOT_APPROVED"
        or review.get("official_generation") != "BLOCKED"
    ):
        raise WithholdingReconciliationError(
            "Unapproved statutory claim"
        )

    if document["sha256"] != digest(body):
        raise WithholdingReconciliationError(
            "Reconciliation fingerprint mismatch"
        )

    return True
