"""Non-posting batch preview of candidate payroll deduction limits.

Verified classifications come from persisted evidence.
Priority and global cap remain technical inputs, NOT legal approval.
No posting, payroll writes, commits, or payment execution.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.payroll_deduction_legal_limit_service import (
    evaluate_verified_deduction_limit,
)


class BatchLimitPreviewError(ValueError):
    """Unsafe or inconsistent candidate deduction allocation."""


@dataclass(frozen=True)
class BatchLimitRequest:
    instruction_id: int
    priority: int
    requested_amount: Decimal


@dataclass(frozen=True)
class BatchLimitLine:
    instruction_id: int
    priority: int
    requested_amount: Decimal
    permitted_amount: Decimal
    unapplied_amount: Decimal


@dataclass(frozen=True)
class BatchLimitPreview:
    lines: tuple[BatchLimitLine, ...]
    total_requested: Decimal
    total_permitted: Decimal
    total_unapplied: Decimal
    remaining_global_capacity: Decimal
    financial_execution_authorized: bool = False
    legal_authorization: str = "NOT_AUTHORIZED"


ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def _amount(value: Decimal) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise BatchLimitPreviewError("A finite Decimal amount is required")

    if value < ZERO:
        raise BatchLimitPreviewError("Negative amounts are not allowed")

    if value != value.quantize(CENT):
        raise BatchLimitPreviewError("Amounts must have cent precision")

    return value.quantize(CENT)


async def preview_payroll_deduction_batch_limits(
    db: AsyncSession,
    *,
    company_id: int,
    statutory_net: Decimal,
    global_aggregate_cap: Decimal,
    requests: tuple[BatchLimitRequest, ...],
) -> BatchLimitPreview:
    """Calculate technical allocation without authorizing withholding."""

    if type(company_id) is not int or company_id <= 0:
        raise BatchLimitPreviewError("Invalid company")

    net = _amount(statutory_net)
    cap = _amount(global_aggregate_cap)

    if cap > net:
        raise BatchLimitPreviewError(
            "Global aggregate cap cannot exceed statutory net"
        )

    if type(requests) is not tuple:
        raise BatchLimitPreviewError("Requests must be a tuple")

    identifiers = set()
    priorities = set()

    # Validate the entire batch before performing any DB reads.
    for request in requests:
        if not isinstance(request, BatchLimitRequest):
            raise BatchLimitPreviewError("Invalid deduction request")

        if type(request.instruction_id) is not int or request.instruction_id <= 0:
            raise BatchLimitPreviewError("Invalid instruction ID")

        if type(request.priority) is not int or request.priority < 0:
            raise BatchLimitPreviewError("Invalid priority")

        _amount(request.requested_amount)

        if request.instruction_id in identifiers:
            raise BatchLimitPreviewError("Duplicate instruction ID")

        if request.priority in priorities:
            raise BatchLimitPreviewError("Ambiguous priority")

        identifiers.add(request.instruction_id)
        priorities.add(request.priority)

    ordered = sorted(
        requests,
        key=lambda item: (item.priority, item.instruction_id),
    )

    lines = []
    withheld = ZERO
    total_requested = ZERO

    for request in ordered:
        requested = _amount(request.requested_amount)
        total_requested += requested

        # Resolve classification/limits through the existing
        # persisted-evidence service, never from caller-provided ratios.
        candidate = await evaluate_verified_deduction_limit(
            db,
            company_id=company_id,
            payroll_deduction_instruction_id=request.instruction_id,
            statutory_net=net,
            requested_amount=requested,
            already_withheld=withheld,
        )

        candidate_amount = _amount(candidate.permitted_amount)

        if candidate_amount > requested:
            raise BatchLimitPreviewError(
                "Verified limit exceeded requested amount"
            )

        global_remaining = cap - withheld

        if global_remaining < ZERO:
            raise BatchLimitPreviewError(
                "Global aggregate capacity became negative"
            )

        permitted = min(
            candidate_amount,
            global_remaining,
            net - withheld,
        )

        unapplied = requested - permitted

        lines.append(
            BatchLimitLine(
                instruction_id=request.instruction_id,
                priority=request.priority,
                requested_amount=requested,
                permitted_amount=permitted,
                unapplied_amount=unapplied,
            )
        )

        withheld += permitted

    total_unapplied = sum(
        (line.unapplied_amount for line in lines),
        ZERO,
    )

    if withheld + total_unapplied != total_requested:
        raise BatchLimitPreviewError(
            "Deduction conservation invariant failed"
        )

    if withheld > cap or withheld > net:
        raise BatchLimitPreviewError(
            "Deduction batch exceeded available capacity"
        )

    return BatchLimitPreview(
        lines=tuple(lines),
        total_requested=total_requested,
        total_permitted=withheld,
        total_unapplied=total_unapplied,
        remaining_global_capacity=cap - withheld,
    )
