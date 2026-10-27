"""Conservative Order 100 classification for two-month severance.

Only independently reconciled salary rows are authorized initially.
Other types require category-specific verification before inclusion.
"""

from dataclasses import dataclass
from decimal import Decimal


KNOWN_TYPES = frozenset({
    "salary",
    "minimum_wage",
    "supplement",
    "manual_adjustment",
    "vacation_pay",
    "sick_pay",
    "leave_compensation",
    "severance",
})

EXCLUDED_TWO_MONTH_TYPES = frozenset({
    "vacation_pay",
    "sick_pay",
    "leave_compensation",
    "severance",
})

REQUIRES_SOURCE_PROOF = frozenset({
    "minimum_wage",
    "supplement",
    "manual_adjustment",
})


@dataclass(frozen=True)
class Order100LineDecision:
    line_type: str
    amount: Decimal
    treatment: str
    reason: str


class Order100ClassificationError(ValueError):
    pass


def classify_order100_line(*, line_type, amount, currency_code):
    if type(line_type) is not str or line_type not in KNOWN_TYPES:
        raise Order100ClassificationError(
            "Unknown payroll component requires legal classification"
        )
    if currency_code != "UAH":
        raise Order100ClassificationError(
            "Order 100 component must be UAH"
        )
    if type(amount) is not Decimal or not amount.is_finite():
        raise Order100ClassificationError(
            "Order 100 component requires finite Decimal amount"
        )

    if line_type == "salary":
        if amount < 0:
            raise Order100ClassificationError(
                "Salary component cannot be negative"
            )
        return Order100LineDecision(
            line_type, amount, "include",
            "Verified ordinary salary; monthly payroll and "
            "attendance reconciliation still required",
        )

    if line_type in EXCLUDED_TWO_MONTH_TYPES:
        if amount < 0:
            raise Order100ClassificationError(
                "Excluded component cannot be negative"
            )
        return Order100LineDecision(
            line_type, amount, "exclude",
            "Excluded from ordinary two-month severance average "
            "under Order 100 section IV",
        )

    if line_type in REQUIRES_SOURCE_PROOF:
        return Order100LineDecision(
            line_type, amount, "requires_proof",
            "Independent classification and source verification "
            "required before inclusion",
        )

    raise Order100ClassificationError(
        "Unclassified payroll component"
    )


def require_supported_severance_lines(lines, *, gross_amount, verified_minimum_wage_amount=None):
    """Fail closed; never silently omit or include unverified pay."""
    if not lines:
        raise Order100ClassificationError(
            "Monthly payroll lines required"
        )
    if type(gross_amount) is not Decimal or not gross_amount.is_finite():
        raise Order100ClassificationError(
            "Finite reconciled gross required"
        )

    decisions = tuple(
        classify_order100_line(
            line_type=getattr(row, "line_type", None),
            amount=getattr(row, "amount", None),
            currency_code=getattr(row, "currency_code", None),
        )
        for row in lines
    )

    gross = sum((d.amount for d in decisions), Decimal("0"))
    if gross != gross_amount:
        if all(d.line_type == "salary" for d in decisions):
            raise Order100ClassificationError(
                "Salary lines do not reconcile to payroll gross"
            )
        raise Order100ClassificationError(
            "Payroll lines do not reconcile to gross"
        )

    if verified_minimum_wage_amount is not None:
        verified = verified_minimum_wage_amount

        if (
            type(verified) is not Decimal
            or not verified.is_finite()
            or verified <= 0
        ):
            raise Order100ClassificationError(
                "Invalid verified minimum-wage amount"
            )

        minimum_lines = tuple(
            d for d in decisions
            if d.line_type == "minimum_wage"
        )

        if (
            len(minimum_lines) != 1
            or minimum_lines[0].amount != verified
        ):
            raise Order100ClassificationError(
                "Verified minimum-wage amount differs from payroll"
            )

        unsupported = tuple(
            d for d in decisions
            if d.line_type not in ("salary", "minimum_wage")
            or (
                d.line_type == "salary"
                and d.treatment != "include"
            )
        )
    else:
        unsupported = tuple(
            d for d in decisions
            if d.treatment != "include"
        )

    if unsupported:
        raise Order100ClassificationError(
            "Non-salary earnings require a verified Order 100 classification"
        )

    if gross <= 0:
        raise Order100ClassificationError(
            "Positive eligible earnings required"
        )

    return gross
