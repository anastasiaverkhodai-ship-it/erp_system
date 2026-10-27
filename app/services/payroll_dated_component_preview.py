"""Preliminary dated-rate arithmetic; not a statutory posting service.

No tax benefits, exemptions, monthly USC limits or multi-contract
allocations are authorized by this helper.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from app.services.payroll_dated_income_allocation import DatedIncomeAllocation


class DatedComponentPreviewError(ValueError):
    pass


@dataclass(frozen=True)
class DatedComponentAmount:
    source_rate_id: int
    source_profile_id: int | None
    source_base_rule_id: int | None
    base_amount: Decimal
    rate: Decimal
    amount: Decimal
    source_line_ids: tuple[int, ...]


def preview_dated_component(
    *,
    allocations: tuple[DatedIncomeAllocation, ...],
    verified_rates: dict[int, Decimal],
    expected_gross: Decimal,
) -> tuple[DatedComponentAmount, ...]:
    """Calculate per-rate-group arithmetic from exact verified income.

    This preview intentionally refuses any profile or base-rule ID:
    those require the full employee-wide statutory policy engine.
    """
    penny = Decimal("0.01")

    if not allocations:
        raise DatedComponentPreviewError("No verified allocations")

    if (
        not isinstance(expected_gross, Decimal)
        or not expected_gross.is_finite()
        or expected_gross < 0
    ):
        raise DatedComponentPreviewError("Invalid expected gross")

    if expected_gross != expected_gross.quantize(penny):
        raise DatedComponentPreviewError("Unrounded expected gross")

    groups = {}
    seen = set()
    total = Decimal("0.00")

    for item in allocations:
        if item.source_line_id in seen or item.source_line_id <= 0:
            raise DatedComponentPreviewError("Duplicate or invalid source")
        seen.add(item.source_line_id)

        if item.source_profile_id is not None:
            raise DatedComponentPreviewError(
                "Profile-dependent tax requires full statutory engine"
            )
        if item.source_base_rule_id is not None:
            raise DatedComponentPreviewError(
                "Base-rule-dependent tax requires full statutory engine"
            )

        base = item.amount
        if (
            not isinstance(base, Decimal)
            or not base.is_finite()
            or base < 0
            or base != base.quantize(penny)
        ):
            raise DatedComponentPreviewError("Invalid income base")

        rate = verified_rates.get(item.source_rate_id)
        if (
            not isinstance(rate, Decimal)
            or not rate.is_finite()
            or not Decimal("0") <= rate <= Decimal("1")
        ):
            raise DatedComponentPreviewError(
                "Missing or invalid verified tax rate"
            )

        total += base
        group = groups.setdefault(
            item.source_rate_id,
            {"rate": rate, "base": Decimal("0"), "sources": []},
        )
        if group["rate"] != rate:
            raise DatedComponentPreviewError("Inconsistent rate provenance")
        group["base"] += base
        group["sources"].append(item.source_line_id)

    if total != expected_gross:
        raise DatedComponentPreviewError(
            "Dated bases do not reconcile to gross"
        )

    results = []
    for rate_id, group in sorted(groups.items()):
        amount = (group["base"] * group["rate"]).quantize(
            penny, rounding=ROUND_HALF_UP
        )
        results.append(
            DatedComponentAmount(
                source_rate_id=rate_id,
                source_profile_id=None,
                source_base_rule_id=None,
                base_amount=group["base"],
                rate=group["rate"],
                amount=amount,
                source_line_ids=tuple(group["sources"]),
            )
        )

    return tuple(results)
