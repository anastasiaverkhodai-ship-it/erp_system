"""Review-only cross-calculation payment allocation.

This module DOES NOT:
- create or confirm a payroll disbursement;
- post or reverse a journal;
- alter a bank/cash reconciliation;
- authorize statutory form generation.

Payables and previous settlements must ultimately come from
verified posted financial sources, never caller-entered figures.
"""

from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json


class CrossCalculationAllocationError(ValueError):
    pass


ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def _id(value, name):
    if type(value) is not int or value <= 0:
        raise CrossCalculationAllocationError(
            f"Invalid {name}"
        )
    return value


def _money(value, name, *, allow_zero=False):
    if not isinstance(value, (str, Decimal)):
        raise CrossCalculationAllocationError(
            f"Invalid monetary type: {name}"
        )

    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError):
        raise CrossCalculationAllocationError(
            f"Invalid amount: {name}"
        ) from None

    if (
        not amount.is_finite()
        or amount.as_tuple().exponent < -2
        or amount < ZERO
        or (not allow_zero and amount == ZERO)
    ):
        raise CrossCalculationAllocationError(
            f"Invalid monetary amount: {name}"
        )

    return amount.quantize(CENT)


def _date(value):
    if not isinstance(value, str):
        raise CrossCalculationAllocationError(
            "Invalid payable date"
        )
    try:
        result = date.fromisoformat(value)
    except ValueError:
        raise CrossCalculationAllocationError(
            "Invalid payable date"
        ) from None

    if result.isoformat() != value:
        raise CrossCalculationAllocationError(
            "Payable date must be ISO format"
        )
    return value


def _digest(value):
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def review_cross_calculation_payment(
    *,
    payment_reference,
    company_id,
    employee_id,
    payment_amount,
    sources,
    previous_settlements=(),
    currency="UAH",
):
    """Deterministic FIFO allocation of a proposed single payment.

    Each source contains company_id, employee_id,
    calculation_id, payable_amount, and due_date.

    Each previous settlement contains settlement_id,
    calculation_id, allocated_amount. These are *claims*
    until verified against immutable posted source records.
    """
    _id(company_id, "company")
    _id(employee_id, "employee")

    if (
        not isinstance(payment_reference, str)
        or not payment_reference.strip()
        or len(payment_reference) > 150
    ):
        raise CrossCalculationAllocationError(
            "Invalid payment reference"
        )

    if currency != "UAH":
        raise CrossCalculationAllocationError(
            "Only UAH is supported"
        )

    amount = _money(payment_amount, "payment amount")

    if not isinstance(sources, (tuple, list)):
        raise CrossCalculationAllocationError(
            "Invalid sources"
        )

    if len(sources) < 2:
        raise CrossCalculationAllocationError(
            "Cross-calculation payment needs at least two sources"
        )

    balances = {}
    ordered = []

    for item in sources:
        if type(item) is not dict:
            raise CrossCalculationAllocationError(
                "Invalid source record"
            )

        if (
            item.get("company_id") != company_id
            or item.get("employee_id") != employee_id
            or type(item.get("company_id")) is not int
            or type(item.get("employee_id")) is not int
        ):
            raise CrossCalculationAllocationError(
                "Cross-company or cross-employee source"
            )

        calculation_id = _id(
            item.get("calculation_id"),
            "calculation",
        )

        if calculation_id in balances:
            raise CrossCalculationAllocationError(
                "Duplicate payroll calculation"
            )

        payable = _money(
            item.get("payable_amount"),
            "payable amount",
        )
        due_date = _date(item.get("due_date"))

        balances[calculation_id] = {
            "total": payable,
            "used": ZERO,
        }
        ordered.append((due_date, calculation_id))

    if not isinstance(previous_settlements, (tuple, list)):
        raise CrossCalculationAllocationError(
            "Invalid prior settlements"
        )

    identifiers = set()

    for item in previous_settlements:
        if type(item) is not dict:
            raise CrossCalculationAllocationError(
                "Invalid prior settlement"
            )

        settlement_id = item.get("settlement_id")
        if (
            not isinstance(settlement_id, str)
            or not settlement_id
            or settlement_id in identifiers
            or settlement_id == payment_reference
        ):
            raise CrossCalculationAllocationError(
                "Duplicate or conflicting settlement identity"
            )

        identifiers.add(settlement_id)
        calculation_id = _id(
            item.get("calculation_id"),
            "prior calculation",
        )

        if calculation_id not in balances:
            raise CrossCalculationAllocationError(
                "Prior settlement belongs to unrelated source"
            )

        used = _money(
            item.get("allocated_amount"),
            "previously allocated amount",
        )
        entry = balances[calculation_id]
        entry["used"] += used

        if entry["used"] > entry["total"]:
            raise CrossCalculationAllocationError(
                "Previous settlements exceed payable"
            )

    remaining = amount
    allocations = []

    for due_date, calculation_id in sorted(ordered):
        entry = balances[calculation_id]
        available = entry["total"] - entry["used"]
        allocated = min(remaining, available)

        if allocated > ZERO:
            allocations.append({
                "calculation_id": calculation_id,
                "due_date": due_date,
                "allocated_amount": f"{allocated:.2f}",
            })
            remaining -= allocated

    if remaining != ZERO:
        raise CrossCalculationAllocationError(
            "Payment exceeds verified payable capacity"
        )

    if sum(
        (
            Decimal(row["allocated_amount"])
            for row in allocations
        ),
        ZERO,
    ) != amount:
        raise CrossCalculationAllocationError(
            "Payment allocation does not reconcile"
        )

    body = {
        "policy": "CROSS_CALC_FIFO_REVIEW_V1",
        "payment_reference": payment_reference,
        "company_id": company_id,
        "employee_id": employee_id,
        "currency": currency,
        "payment_amount": f"{amount:.2f}",
        "sources": [
            {
                "calculation_id": calc_id,
                "due_date": due_date,
                "payable_amount": (
                    f"{balances[calc_id]['total']:.2f}"
                ),
                "previously_allocated": (
                    f"{balances[calc_id]['used']:.2f}"
                ),
            }
            for due_date, calc_id in sorted(ordered)
        ],
        "allocations": allocations,
        "review_status": "REVIEW_ONLY",
        "posting_authorized": False,
        "official_generation": "BLOCKED",
    }

    return {
        "body": body,
        "sha256": _digest(body),
    }


def verify_cross_calculation_review(record):
    """Detect modified or forged internal review envelopes.

    This is NOT a substitute for live accounting source checks.
    """
    if type(record) is not dict:
        raise CrossCalculationAllocationError(
            "Invalid review record"
        )

    body = record.get("body")
    digest = record.get("sha256")

    if (
        type(body) is not dict
        or not isinstance(digest, str)
        or _digest(body) != digest
        or body.get("policy")
            != "CROSS_CALC_FIFO_REVIEW_V1"
        or body.get("review_status") != "REVIEW_ONLY"
        or body.get("posting_authorized") is not False
        or body.get("official_generation") != "BLOCKED"
    ):
        raise CrossCalculationAllocationError(
            "Invalid or modified review evidence"
        )

    expected = review_cross_calculation_payment(
        payment_reference=body["payment_reference"],
        company_id=body["company_id"],
        employee_id=body["employee_id"],
        payment_amount=body["payment_amount"],
        sources=[
            {
                "company_id": body["company_id"],
                "employee_id": body["employee_id"],
                "calculation_id": item["calculation_id"],
                "due_date": item["due_date"],
                "payable_amount": item["payable_amount"],
            }
            for item in body["sources"]
        ],
        previous_settlements=[
            {
                "settlement_id": f"verified-prior:{i}",
                "calculation_id": item["calculation_id"],
                "allocated_amount": item["previously_allocated"],
            }
            for i, item in enumerate(body["sources"])
            if Decimal(item["previously_allocated"]) > ZERO
        ],
        currency=body["currency"],
    )

    if expected != record:
        raise CrossCalculationAllocationError(
            "Review allocation is not reproducible"
        )

    return body
