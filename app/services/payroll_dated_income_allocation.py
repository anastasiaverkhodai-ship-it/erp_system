"""Fail-closed attribution of verified dated payroll income.

This module maps existing source amounts to effective policy windows.
It does not invent an amount split or calculate statutory tax.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


class DatedIncomeAllocationError(ValueError):
    pass


@dataclass(frozen=True)
class DatedIncome:
    source_line_id: int
    effective_from: date
    effective_to: date
    amount: Decimal


@dataclass(frozen=True)
class TaxPolicyWindow:
    effective_from: date
    effective_to: date
    source_rate_id: int
    source_profile_id: int | None
    source_base_rule_id: int | None


@dataclass(frozen=True)
class DatedIncomeAllocation:
    source_line_id: int
    effective_from: date
    effective_to: date
    amount: Decimal
    source_rate_id: int
    source_profile_id: int | None
    source_base_rule_id: int | None


def allocate_verified_dated_income(
    *,
    income_lines: list[DatedIncome],
    policy_windows: list[TaxPolicyWindow],
) -> tuple[DatedIncomeAllocation, ...]:
    """Attribute each exact source amount to one complete policy window.

    A source line crossing a policy boundary must first be split by
    the authoritative payroll calculation. Calendar-day proration
    is deliberately prohibited.
    """

    if not income_lines:
        raise DatedIncomeAllocationError(
            "Verified dated income lines are required"
        )

    if not policy_windows:
        raise DatedIncomeAllocationError(
            "Dated tax policy windows are required"
        )

    windows = sorted(
        policy_windows,
        key=lambda row: (row.effective_from, row.effective_to),
    )

    for index, policy in enumerate(windows):
        if (
            policy.effective_from > policy.effective_to
            or type(policy.source_rate_id) is not int
            or policy.source_rate_id <= 0
        ):
            raise DatedIncomeAllocationError(
                "Invalid dated tax policy window"
            )

        for identifier in (
            policy.source_profile_id,
            policy.source_base_rule_id,
        ):
            if identifier is not None and (
                type(identifier) is not int or identifier <= 0
            ):
                raise DatedIncomeAllocationError(
                    "Invalid tax policy provenance identifier"
                )

        if index and windows[index - 1].effective_to >= policy.effective_from:
            raise DatedIncomeAllocationError(
                "Overlapping dated tax policy windows"
            )

    seen = set()
    allocations = []

    for income in income_lines:
        if (
            type(income.source_line_id) is not int
            or income.source_line_id <= 0
            or income.source_line_id in seen
        ):
            raise DatedIncomeAllocationError(
                "Missing or duplicate income source line"
            )

        seen.add(income.source_line_id)

        if income.effective_from > income.effective_to:
            raise DatedIncomeAllocationError(
                "Invalid income source date range"
            )

        if (
            not isinstance(income.amount, Decimal)
            or not income.amount.is_finite()
            or income.amount < Decimal("0.00")
            or income.amount != income.amount.quantize(
                Decimal("0.01")
            )
        ):
            raise DatedIncomeAllocationError(
                "Invalid verified income amount"
            )

        matching = [
            policy for policy in windows
            if (
                policy.effective_from <= income.effective_from
                and income.effective_to <= policy.effective_to
            )
        ]

        if len(matching) != 1:
            raise DatedIncomeAllocationError(
                "Income crosses a tax policy boundary or lacks "
                "complete dated coverage; verified source "
                "allocation is required"
            )

        policy = matching[0]

        allocations.append(
            DatedIncomeAllocation(
                source_line_id=income.source_line_id,
                effective_from=income.effective_from,
                effective_to=income.effective_to,
                amount=income.amount,
                source_rate_id=policy.source_rate_id,
                source_profile_id=policy.source_profile_id,
                source_base_rule_id=policy.source_base_rule_id,
            )
        )

    source_total = sum(
        (line.amount for line in income_lines),
        Decimal("0.00"),
    )
    allocated_total = sum(
        (line.amount for line in allocations),
        Decimal("0.00"),
    )

    if source_total != allocated_total:
        raise DatedIncomeAllocationError(
            "Dated income attribution does not reconcile"
        )

    return tuple(allocations)
