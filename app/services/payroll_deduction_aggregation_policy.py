"""Phase 13 C2-B: deduction aggregation policy contract.

This module builds a fail-closed technical allocation preview.

IMPORTANT:
- Legal categories are identifiers, not approved legal priorities.
- Priority values are caller-supplied technical inputs.
- Limits are caller-supplied technical inputs.
- No statutory percentage is inferred here.
- No payroll deductions are posted.
- No database records are modified.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.services.payroll_deduction_aggregation_service import (
    DeductionClaim,
    DeductionAggregationResult,
    calculate_deduction_aggregation,
)


LEGAL_CATEGORIES = frozenset({
    "employer_statutory",
    "enforcement_other",
    "enforcement_special",
    "minor_child_alimony",
})


class DeductionPolicyError(ValueError):
    """Policy input is invalid or cannot be safely allocated."""


@dataclass(frozen=True)
class DeductionPolicyLine:
    instruction_id: int
    legal_category: str
    priority: int
    requested_amount: Decimal
    individual_limit: Decimal


@dataclass(frozen=True)
class DeductionPolicyPreview:
    result: DeductionAggregationResult
    legal_authorization: str
    payroll_posting_allowed: bool


def _money(value, field):
    if type(value) is not Decimal:
        raise DeductionPolicyError(
            f"{field} must be Decimal"
        )

    if not value.is_finite():
        raise DeductionPolicyError(
            f"{field} must be finite"
        )

    if value < 0:
        raise DeductionPolicyError(
            f"{field} must be nonnegative"
        )

    # No rounding is silently performed.
    # Decimal("1.000") is allowed because it is exactly
    # representable to two decimal places.
    if value.as_tuple().exponent < -2:
        if value * 100 != (value * 100).to_integral_value():
            raise DeductionPolicyError(
                f"{field} exceeds cent precision"
            )

    return value


def _validate_lines(lines):
    if type(lines) is not tuple:
        raise DeductionPolicyError(
            "Policy lines must be a tuple"
        )

    instruction_ids = set()
    priorities = set()

    for line in lines:
        if not isinstance(line, DeductionPolicyLine):
            raise DeductionPolicyError(
                "Invalid policy line"
            )

        if (
            type(line.instruction_id) is not int
            or line.instruction_id <= 0
        ):
            raise DeductionPolicyError(
                "Invalid instruction ID"
            )

        if (
            type(line.legal_category) is not str
            or line.legal_category not in LEGAL_CATEGORIES
        ):
            raise DeductionPolicyError(
                "Unknown legal category"
            )

        if (
            type(line.priority) is not int
            or line.priority < 0
        ):
            raise DeductionPolicyError(
                "Invalid priority"
            )

        if line.instruction_id in instruction_ids:
            raise DeductionPolicyError(
                "Duplicate instruction ID"
            )

        if line.priority in priorities:
            raise DeductionPolicyError(
                "Ambiguous equal-priority allocation"
            )

        instruction_ids.add(line.instruction_id)
        priorities.add(line.priority)

        _money(
            line.requested_amount,
            "requested_amount",
        )

        _money(
            line.individual_limit,
            "individual_limit",
        )


def preview_deduction_policy(
    *,
    available_amount: Decimal,
    aggregate_limit: Decimal,
    lines: tuple[DeductionPolicyLine, ...],
) -> DeductionPolicyPreview:
    """Validate inputs and simulate allocations without posting."""

    _money(
        available_amount,
        "available_amount",
    )

    _money(
        aggregate_limit,
        "aggregate_limit",
    )

    _validate_lines(lines)

    claims = tuple(
        DeductionClaim(
            instruction_id=line.instruction_id,
            priority=line.priority,
            requested_amount=line.requested_amount,
            individual_limit=line.individual_limit,
        )
        for line in lines
    )

    try:
        result = calculate_deduction_aggregation(
            available_amount=available_amount,
            aggregate_limit=aggregate_limit,
            claims=claims,
        )
    except ValueError as exc:
        raise DeductionPolicyError(
            f"Aggregation rejected policy: {exc}"
        ) from exc

    if result.total_allocated > available_amount:
        raise DeductionPolicyError(
            "Allocated amount exceeds available amount"
        )

    if result.total_allocated > aggregate_limit:
        raise DeductionPolicyError(
            "Allocated amount exceeds aggregate limit"
        )

    total_requested = sum(
        (line.requested_amount for line in lines),
        Decimal("0.00"),
    )

    total_unallocated = sum(
        (
            allocation.remaining_amount
            for allocation in result.allocations
        ),
        Decimal("0.00"),
    )

    if (
        result.total_allocated + total_unallocated
        != total_requested
    ):
        raise DeductionPolicyError(
            "Deduction conservation invariant failed"
        )

    for allocation in result.allocations:
        if allocation.allocated_amount < 0:
            raise DeductionPolicyError(
                "Negative allocation"
            )

        if allocation.remaining_amount < 0:
            raise DeductionPolicyError(
                "Negative remainder"
            )

        if (
            allocation.allocated_amount
            + allocation.remaining_amount
            != allocation.requested_amount
        ):
            raise DeductionPolicyError(
                "Line conservation invariant failed"
            )

    return DeductionPolicyPreview(
        result=result,
        legal_authorization="NOT_AUTHORIZED",
        payroll_posting_allowed=False,
    )
