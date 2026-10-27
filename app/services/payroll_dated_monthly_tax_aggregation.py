"""Fail-closed aggregation of saved monthly employee tax lines.

Pure validation only. Does not certify employee-wide calculation,
source attribution, ledger reconciliation, or enable DB persistence.
"""

from datetime import timedelta
from decimal import Decimal
from collections import defaultdict


class DatedMonthlyTaxAggregationError(ValueError):
    pass


COMPONENTS = frozenset({
    "personal_income_tax",
    "military_levy",
})


def aggregate_dated_monthly_employee_taxes(
    lines,
    *,
    components=COMPONENTS,
):
    expected = frozenset(components)
    if expected != COMPONENTS:
        raise DatedMonthlyTaxAggregationError(
            "Unsupported employee component contract"
        )

    grouped = defaultdict(list)

    for line in lines:
        component = line.component

        # Employer contributions are not employee withholding.
        if component == "unified_social_contribution":
            continue

        if component not in expected:
            raise DatedMonthlyTaxAggregationError(
                "Unsupported monthly statutory component"
            )

        if line.currency_code != "UAH":
            raise DatedMonthlyTaxAggregationError(
                "Monthly component currency mismatch"
            )

        amount = Decimal(line.amount)
        if (
            not amount.is_finite()
            or amount < 0
            or amount != amount.quantize(Decimal("0.01"))
        ):
            raise DatedMonthlyTaxAggregationError(
                "Invalid monthly statutory amount"
            )

        grouped[component].append(line)

    if set(grouped) != expected:
        raise DatedMonthlyTaxAggregationError(
            "Both monthly employee taxes are required"
        )

    result = {}

    for component in sorted(expected):
        rows = grouped[component]

        if len(rows) > 1:
            # A duplicated line must not inflate an advance balance.
            # The periods must be proven disjoint and contiguous.
            intervals = []
            seen_ids = set()

            for line in rows:
                rate_id = getattr(line, "source_rate_id", None)
                start = getattr(line, "rate_effective_from", None)
                end = getattr(line, "rate_effective_to", None)

                if (
                    rate_id is None
                    or rate_id in seen_ids
                    or start is None
                    or end is None
                    or start > end
                ):
                    raise DatedMonthlyTaxAggregationError(
                        "Ambiguous dated monthly rate provenance"
                    )

                if (
                    getattr(line, "source_tax_profile_id", None)
                    is not None
                    or getattr(line, "source_base_rule_id", None)
                    is not None
                ):
                    raise DatedMonthlyTaxAggregationError(
                        "Special monthly tax policy requires "
                        "employee-wide dated reconciliation"
                    )

                seen_ids.add(rate_id)
                intervals.append((start, end))

            intervals.sort()

            for previous, current in zip(
                intervals,
                intervals[1:],
            ):
                if (
                    previous[1] + timedelta(days=1)
                    != current[0]
                ):
                    raise DatedMonthlyTaxAggregationError(
                        "Overlapping or discontinuous monthly rates"
                    )

        result[component] = sum(
            (Decimal(line.amount) for line in rows),
            Decimal("0.00"),
        )

    return result
