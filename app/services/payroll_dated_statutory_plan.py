"""Validated, read-only plan for dated statutory tax result lines.

This is not a tax-policy resolver or a database writer.
Only already verified plain-gross allocations may enter this plan.
Employee-wide benefits, USC limits, severance and multiple contracts
must be validated by the statutory integration layer before persistence.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Mapping, Sequence

from app.services.payroll_dated_component_preview import (
    DatedComponentAmount,
)


PIT = "personal_income_tax"
LEVY = "military_levy"
USC = "unified_social_contribution"
COMPONENTS = (PIT, LEVY, USC)
CENT = Decimal("0.01")


class DatedStatutoryPlanError(ValueError):
    pass


@dataclass(frozen=True)
class PlannedStatutoryLine:
    line_no: int
    component: str
    source_rate_id: int
    source_profile_id: int | None
    source_base_rule_id: int | None
    source_line_ids: tuple[int, ...]
    base_amount: Decimal
    rate: Decimal
    amount: Decimal


@dataclass(frozen=True)
class DatedStatutoryPlan:
    gross_amount: Decimal
    currency_code: str
    employee_withholding_amount: Decimal
    employer_contribution_amount: Decimal
    net_amount: Decimal
    lines: tuple[PlannedStatutoryLine, ...]


def _money(value: Decimal, field: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise DatedStatutoryPlanError(f"Invalid {field}")
    if value < 0 or value != value.quantize(CENT):
        raise DatedStatutoryPlanError(f"Invalid monetary {field}")
    return value


def build_dated_statutory_plan(
    *,
    gross_amount: Decimal,
    currency_code: str,
    components: Mapping[str, Sequence[DatedComponentAmount]],
) -> DatedStatutoryPlan:
    """Validate a plain-income three-component result without writing.

    The caller must independently prove salary-only sources, company,
    employee, rate applicability, no special profiles or base policies,
    no severance, and no multi-contract allocation requirement.
    """
    gross = _money(gross_amount, "gross")
    if currency_code != "UAH":
        raise DatedStatutoryPlanError("Statutory plan requires UAH")
    if not isinstance(components, Mapping) or set(components) != set(COMPONENTS):
        raise DatedStatutoryPlanError("Exactly three statutory components required")

    result = []
    amounts = {}

    for component in COMPONENTS:
        rows = components[component]
        if not rows:
            raise DatedStatutoryPlanError(f"Missing {component} allocations")

        component_base = Decimal("0.00")
        component_amount = Decimal("0.00")
        seen_sources = set()
        seen_rates = set()

        for row in rows:
            if not isinstance(row, DatedComponentAmount):
                raise DatedStatutoryPlanError("Unverified row type")
            if row.source_profile_id is not None or row.source_base_rule_id is not None:
                raise DatedStatutoryPlanError(
                    "Special tax policy requires employee-wide engine"
                )
            if (
                isinstance(row.source_rate_id, bool)
                or not isinstance(row.source_rate_id, int)
                or row.source_rate_id <= 0
                or row.source_rate_id in seen_rates
            ):
                raise DatedStatutoryPlanError("Invalid or repeated rate provenance")
            seen_rates.add(row.source_rate_id)

            if (
                not isinstance(row.source_line_ids, tuple)
                or not row.source_line_ids
                or any(
                    isinstance(source_id, bool)
                    or not isinstance(source_id, int)
                    or source_id <= 0
                    or source_id in seen_sources
                    for source_id in row.source_line_ids
                )
                or len(set(row.source_line_ids)) != len(row.source_line_ids)
            ):
                raise DatedStatutoryPlanError(
                    "Missing or duplicate salary source provenance"
                )
            seen_sources.update(row.source_line_ids)

            base = _money(row.base_amount, "component base")
            amount = _money(row.amount, "component amount")
            rate = row.rate
            if (
                not isinstance(rate, Decimal)
                or not rate.is_finite()
                or not Decimal("0") <= rate <= Decimal("1")
            ):
                raise DatedStatutoryPlanError("Invalid rate")

            expected = (base * rate).quantize(CENT, rounding=ROUND_HALF_UP)
            if expected != amount:
                raise DatedStatutoryPlanError("Amount does not match verified rate")

            result.append(
                PlannedStatutoryLine(
                    line_no=len(result) + 1,
                    component=component,
                    source_rate_id=row.source_rate_id,
                    source_profile_id=None,
                    source_base_rule_id=None,
                    source_line_ids=row.source_line_ids,
                    base_amount=base,
                    rate=rate,
                    amount=amount,
                )
            )
            component_base += base
            component_amount += amount

        if component_base != gross:
            raise DatedStatutoryPlanError(
                f"{component} bases do not reconcile to gross"
            )
        amounts[component] = component_amount

    withholding = amounts[PIT] + amounts[LEVY]
    contribution = amounts[USC]
    net = gross - withholding

    if net < 0:
        raise DatedStatutoryPlanError("Withholding exceeds gross salary")

    return DatedStatutoryPlan(
        gross_amount=gross,
        currency_code=currency_code,
        employee_withholding_amount=withholding,
        employer_contribution_amount=contribution,
        net_amount=net,
        lines=tuple(result),
    )
