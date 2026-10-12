"""Fail-closed planning engine for payroll deduction aggregation.

No statutory limits or legal priority are inferred by this module.
It cannot authorize, persist, or execute payroll deductions.
"""

from dataclasses import dataclass
from decimal import Decimal

CENT = Decimal("0.01")


class DeductionAggregationError(ValueError):
    pass


@dataclass(frozen=True)
class DeductionClaim:
    instruction_id: int
    priority: int
    requested_amount: Decimal
    individual_limit: Decimal


@dataclass(frozen=True)
class DeductionAllocation:
    instruction_id: int
    priority: int
    requested_amount: Decimal
    allocated_amount: Decimal
    remaining_amount: Decimal


@dataclass(frozen=True)
class DeductionAggregationResult:
    available_amount: Decimal
    aggregate_limit: Decimal
    total_allocated: Decimal
    available_after_allocations: Decimal
    total_unallocated: Decimal
    allocations: tuple[DeductionAllocation, ...]


def _money(value: Decimal, name: str) -> Decimal:
    if type(value) is not Decimal:
        raise DeductionAggregationError(
            f"{name} must be Decimal"
        )

    if not value.is_finite() or value < 0:
        raise DeductionAggregationError(
            f"{name} must be finite and nonnegative"
        )

    if value != value.quantize(CENT):
        raise DeductionAggregationError(
            f"{name} must use no more than two decimal places"
        )

    return value


def calculate_deduction_aggregation(
    *,
    available_amount: Decimal,
    aggregate_limit: Decimal,
    claims: tuple[DeductionClaim, ...],
) -> DeductionAggregationResult:
    """Produce a deterministic allocation simulation.

    Smaller priority numbers are processed first.
    Equal priorities use instruction_id as a deterministic
    technical tie-breaker, NOT a legally approved rule.
    """

    available = _money(available_amount, "available_amount")
    aggregate = _money(aggregate_limit, "aggregate_limit")

    if type(claims) is not tuple:
        raise DeductionAggregationError(
            "claims must be a tuple"
        )

    seen = set()

    for claim in claims:
        if not isinstance(claim, DeductionClaim):
            raise DeductionAggregationError(
                "Invalid deduction claim"
            )

        if type(claim.instruction_id) is not int or claim.instruction_id <= 0:
            raise DeductionAggregationError(
                "Invalid instruction ID"
            )

        if type(claim.priority) is not int or claim.priority < 0:
            raise DeductionAggregationError(
                "Invalid priority"
            )

        if claim.instruction_id in seen:
            raise DeductionAggregationError(
                "Duplicate instruction ID"
            )

        seen.add(claim.instruction_id)

        _money(claim.requested_amount, "requested_amount")
        _money(claim.individual_limit, "individual_limit")

    budget = min(available, aggregate)
    remainder = budget
    allocations = []

    for claim in sorted(
        claims,
        key=lambda x: (x.priority, x.instruction_id),
    ):
        allocated = min(
            claim.requested_amount,
            claim.individual_limit,
            remainder,
        )

        remainder -= allocated

        allocations.append(
            DeductionAllocation(
                instruction_id=claim.instruction_id,
                priority=claim.priority,
                requested_amount=claim.requested_amount,
                allocated_amount=allocated,
                remaining_amount=claim.requested_amount - allocated,
            )
        )

    total = budget - remainder

    return DeductionAggregationResult(
        available_amount=available,
        aggregate_limit=aggregate,
        total_allocated=total,
        available_after_allocations=available - total,
        total_unallocated=sum(
            (item.remaining_amount for item in allocations),
            Decimal("0.00"),
        ),
        allocations=tuple(allocations),
    )
