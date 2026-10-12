"""Read-only, fail-closed assessment of a supplied deduction snapshot.

Candidate arithmetic only: never authorizes withholding, allocates shortages,
infers legal priority, or changes payroll. The caller must supply ALL proposed
and already-withheld amounts for one contract, period, currency and net base.
Completeness, concurrent changes and legal applicability require separate
acceptance before any future integration with payroll.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll_deduction import PayrollDeductionInstruction
from app.services.payroll_deduction_evidence_policy import (
    resolve_verified_deduction_classification,
)
from app.services.payroll_deduction_verified_limit import (
    calculate_verified_deduction_limit,
)


class PayrollAggregateSafetyError(ValueError):
    """The complete supplied snapshot cannot pass candidate safety checks."""


@dataclass(frozen=True)
class DeductionAggregateItem:
    payroll_deduction_instruction_id: int
    requested_amount: Decimal
    already_withheld: Decimal = Decimal("0.00")


@dataclass(frozen=True)
class DeductionAggregateAssessment:
    category: str | None
    requested_total: Decimal
    already_withheld_total: Decimal
    combined_total: Decimal
    aggregate_maximum: Decimal
    remaining_capacity: Decimal
    # Passing arithmetic is never permission to change an actual payroll.
    authorizes_payroll: bool = False


def _positive_id(value: int) -> bool:
    return type(value) is int and value > 0


def _amount(value: Decimal) -> Decimal:
    # Reject sub-cent inputs instead of silently rounding away overages or
    # negative values. This API accepts normalized payroll amounts only.
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise PayrollAggregateSafetyError("Amounts must be finite nonnegative Decimals")
    try:
        normalized = value.quantize(Decimal("0.01"))
    except ArithmeticError as exc:
        raise PayrollAggregateSafetyError("Invalid monetary precision") from exc
    if value != normalized:
        raise PayrollAggregateSafetyError("Amounts must use whole cents")
    return normalized


async def assess_payroll_deduction_aggregate(
    db: AsyncSession,
    *,
    company_id: int,
    employment_contract_id: int,
    effective_date: date,
    currency_code: str,
    statutory_net: Decimal,
    items: tuple[DeductionAggregateItem, ...],
) -> DeductionAggregateAssessment:
    """Reject the whole snapshot if any item or combined amount is unsafe.

    Prior withholding is bound to its instruction and verified category; no
    anonymous aggregate balance is accepted. Individual caps cover prior plus
    proposed amounts for that instruction. Mixed categories are rejected even
    if their numeric aggregate percentages happen to match. No partial result
    or priority-dependent allocation is returned on failure.
    """
    if not _positive_id(company_id) or not _positive_id(employment_contract_id):
        raise PayrollAggregateSafetyError("Invalid company or employment contract")
    if type(effective_date) is not date or currency_code != "UAH":
        raise PayrollAggregateSafetyError("A date and supported UAH currency are required")
    net = _amount(statutory_net)
    if not isinstance(items, tuple):
        raise PayrollAggregateSafetyError("An immutable tuple of snapshot items is required")
    seen = set()
    for item in items:
        if not isinstance(item, DeductionAggregateItem):
            raise PayrollAggregateSafetyError("Invalid snapshot item")
        instruction_id = item.payroll_deduction_instruction_id
        if not _positive_id(instruction_id) or instruction_id in seen:
            raise PayrollAggregateSafetyError("Invalid or duplicate instruction")
        seen.add(instruction_id)
        _amount(item.requested_amount)
        _amount(item.already_withheld)

    category = None
    maximum = Decimal("0.00")
    requested = Decimal("0.00")
    withheld = Decimal("0.00")
    # A read must not autoflush unrelated pending changes on the caller's session.
    with db.no_autoflush:
        for item in items:
            instruction = await db.scalar(
                select(PayrollDeductionInstruction).where(
                    PayrollDeductionInstruction.company_id == company_id,
                    PayrollDeductionInstruction.employment_contract_id == employment_contract_id,
                    PayrollDeductionInstruction.id == item.payroll_deduction_instruction_id,
                )
            )
            if instruction is None:
                raise PayrollAggregateSafetyError("Instruction not found in company/contract scope")
            if (
                instruction.company_id != company_id
                or instruction.employment_contract_id != employment_contract_id
                or instruction.id != item.payroll_deduction_instruction_id
                or instruction.currency_code != currency_code
                or instruction.effective_from > effective_date
                or (instruction.effective_to is not None
                    and instruction.effective_to < effective_date)
            ):
                raise PayrollAggregateSafetyError("Instruction scope, currency or dates do not match")
            try:
                classification = await resolve_verified_deduction_classification(
                    db,
                    company_id=company_id,
                    payroll_deduction_instruction_id=item.payroll_deduction_instruction_id,
                )
                if category is not None and classification.category != category:
                    raise PayrollAggregateSafetyError("Mixed legal categories require separate acceptance")
                category = classification.category
                combined = _amount(item.requested_amount + item.already_withheld)
                limit = calculate_verified_deduction_limit(
                    statutory_net=net,
                    requested_amount=combined,
                    already_withheld=Decimal("0.00"),
                    classification=classification,
                )
            except (ValueError, TypeError, ArithmeticError) as exc:
                raise PayrollAggregateSafetyError(str(exc)) from exc
            if combined > limit.individual_maximum:
                raise PayrollAggregateSafetyError("Instruction exceeds candidate individual limit")
            maximum = limit.aggregate_maximum
            requested = _amount(requested + item.requested_amount)
            withheld = _amount(withheld + item.already_withheld)

    total = _amount(requested + withheld)
    if total > maximum or total > net:
        raise PayrollAggregateSafetyError("Combined deductions exceed candidate aggregate limit")
    return DeductionAggregateAssessment(
        category=category,
        requested_total=requested,
        already_withheld_total=withheld,
        combined_total=total,
        aggregate_maximum=maximum,
        remaining_capacity=_amount(maximum - total),
    )
