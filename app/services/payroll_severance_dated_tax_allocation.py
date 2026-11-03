"""Review-only dated severance allocation.

Never derive income-specific tax from gross proportions.
An allocation that reconciles arithmetically is not by itself
verified statutory evidence or authorization for posting.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation


COMPONENTS = frozenset({
    "personal_income_tax",
    "military_levy",
    "unified_social_contribution",
})

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


class SeveranceAllocationError(ValueError):
    pass


def exact_money(value):
    if isinstance(value, (bool, float)):
        raise SeveranceAllocationError(
            "Exact Decimal money is required"
        )
    try:
        number = Decimal(str(value))
        if (
            not number.is_finite()
            or number < ZERO
            or number != number.quantize(CENT)
        ):
            raise SeveranceAllocationError(
                "Nonnegative, finite two-decimal money required"
            )
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SeveranceAllocationError(
            "Invalid allocation money"
        ) from exc
    return number


@dataclass(frozen=True)
class ComponentAllocation:
    component: str
    statutory_total: Decimal
    ordinary_amount: Decimal
    severance_amount: Decimal
    evidence_reference: str


@dataclass(frozen=True)
class SeveranceAllocationReview:
    company_id: int
    payroll_calculation_id: int
    income_date: date
    currency_code: str
    allocations: tuple[ComponentAllocation, ...]
    reconciled: bool
    independent_evidence_approved: bool = False
    financial_execution_authorized: bool = False
    statutory_correction_authorized: bool = False

    def component(self, name):
        for row in self.allocations:
            if row.component == name:
                return row
        raise SeveranceAllocationError(
            "Statutory component unavailable"
        )


def build_dated_severance_allocation_review(
    *,
    company_id,
    payroll_calculation_id,
    income_date,
    currency_code,
    rows,
):
    if (
        type(company_id) is not int
        or company_id <= 0
        or type(payroll_calculation_id) is not int
        or payroll_calculation_id <= 0
    ):
        raise SeveranceAllocationError(
            "Invalid company or calculation identity"
        )

    if type(income_date) is not date:
        raise SeveranceAllocationError(
            "A dated income source is required"
        )

    if currency_code != "UAH":
        raise SeveranceAllocationError(
            "Only UAH statutory allocation is supported"
        )

    if not isinstance(rows, (list, tuple)):
        raise SeveranceAllocationError(
            "Explicit component allocation rows required"
        )

    if len(rows) != len(COMPONENTS):
        raise SeveranceAllocationError(
            "Exactly three statutory components required"
        )

    validated = []
    seen = set()

    for row in rows:
        if not isinstance(row, ComponentAllocation):
            raise SeveranceAllocationError(
                "Typed allocation evidence required"
            )

        if row.component not in COMPONENTS:
            raise SeveranceAllocationError(
                "Unsupported statutory component"
            )

        if row.component in seen:
            raise SeveranceAllocationError(
                "Duplicate statutory component"
            )

        if (
            not isinstance(row.evidence_reference, str)
            or not row.evidence_reference.strip()
        ):
            raise SeveranceAllocationError(
                "An allocation evidence reference is required"
            )

        total = exact_money(row.statutory_total)
        ordinary = exact_money(row.ordinary_amount)
        severance = exact_money(row.severance_amount)

        if ordinary + severance != total:
            raise SeveranceAllocationError(
                f"{row.component}: allocated amounts "
                "do not reconcile to statutory total"
            )

        validated.append(
            ComponentAllocation(
                component=row.component,
                statutory_total=total,
                ordinary_amount=ordinary,
                severance_amount=severance,
                evidence_reference=row.evidence_reference.strip(),
            )
        )
        seen.add(row.component)

    if seen != COMPONENTS:
        raise SeveranceAllocationError(
            "Incomplete statutory components"
        )

    return SeveranceAllocationReview(
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
        income_date=income_date,
        currency_code=currency_code,
        allocations=tuple(
            sorted(validated, key=lambda item: item.component)
        ),
        reconciled=True,
    )
