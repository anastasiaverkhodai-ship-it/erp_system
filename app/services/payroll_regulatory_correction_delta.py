"""Financial delta between original and replacement payroll revisions.

Historical amounts are retained separately from active amounts.
No official D1/4DF filing readiness is implied.
"""

from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.services.payroll_regulatory_financial_reconciliation import (
    FinancialReconciliationError,
)

CENT = Decimal("0.01")


def amount(value):
    if isinstance(value, bool) or value is None:
        raise FinancialReconciliationError("Invalid financial amount")
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError("Nonfinite amount")
        if number != number.quantize(CENT):
            raise ValueError("Excess monetary precision")
        return number.quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise FinancialReconciliationError(
            "Invalid financial amount"
        ) from exc


def positive(value, field):
    if type(value) is not int or value <= 0:
        raise FinancialReconciliationError(
            f"Invalid {field}"
        )
    return value


def reconcile_correction_delta(
    chain,
    statutory_lines,
    source_lines,
    *,
    company_id,
    payroll_period_id,
):
    positive(company_id, "company")
    positive(payroll_period_id, "period")

    if not isinstance(chain, dict):
        raise FinancialReconciliationError("Invalid chain")

    if not isinstance(statutory_lines, list):
        raise FinancialReconciliationError("Invalid statutory lines")

    if not isinstance(source_lines, list):
        raise FinancialReconciliationError("Invalid source lines")

    if (
        chain.get("company_id") != company_id
        or chain.get("payroll_period_id") != payroll_period_id
        or chain.get("status") != "REVISION_CHAIN_VERIFIED"
        or chain.get("official_generation") != "BLOCKED"
        or chain.get("data_origin") != "PERSISTED_POSTGRESQL"
    ):
        raise FinancialReconciliationError(
            "Unverified revision chain"
        )

    active = chain.get("active_calculation_ids")
    historical = chain.get("historical_calculation_ids")

    if not isinstance(active, list) or not isinstance(historical, list):
        raise FinancialReconciliationError(
            "Invalid revision partition"
        )

    for identifier in active + historical:
        positive(identifier, "calculation ID")

    if (
        not active
        or not historical
        or len(active + historical) != len(set(active + historical))
        or chain.get("calculation_count") != len(active + historical)
    ):
        raise FinancialReconciliationError(
            "Incomplete correction revision partition"
        )

    expected_ids = set(active + historical)
    by_line = {}
    components = defaultdict(
        lambda: {
            "active_base": Decimal("0.00"),
            "historical_base": Decimal("0.00"),
            "active_tax": Decimal("0.00"),
            "historical_tax": Decimal("0.00"),
            "active_sources": Decimal("0.00"),
            "historical_sources": Decimal("0.00"),
        }
    )

    for line in statutory_lines:
        if not isinstance(line, dict):
            raise FinancialReconciliationError(
                "Invalid statutory line"
            )

        line_id = positive(line.get("id"), "statutory line")
        calculation_id = positive(
            line.get("payroll_calculation_id"),
            "calculation",
        )

        if line_id in by_line:
            raise FinancialReconciliationError(
                "Duplicate statutory line ID"
            )

        if (
            line.get("company_id") != company_id
            or calculation_id not in expected_ids
        ):
            raise FinancialReconciliationError(
                "Out-of-scope statutory line"
            )

        component = line.get("component")
        if not isinstance(component, str) or not component.strip():
            raise FinancialReconciliationError(
                "Invalid statutory component"
            )

        currency = line.get("currency_code")
        if not isinstance(currency, str) or len(currency) != 3:
            raise FinancialReconciliationError(
                "Invalid currency"
            )

        key = (component, currency)
        side = (
            "active" if calculation_id in active else "historical"
        )

        components[key][side + "_base"] += amount(
            line.get("base_amount")
        )
        components[key][side + "_tax"] += amount(
            line.get("amount")
        )

        by_line[line_id] = {
            "calculation_id": calculation_id,
            "key": key,
            "side": side,
            "base": amount(line.get("base_amount")),
            "sources": Decimal("0.00"),
            "source_count": 0,
        }

    source_ids = set()
    source_pairs = set()

    for source in source_lines:
        if not isinstance(source, dict):
            raise FinancialReconciliationError(
                "Invalid source"
            )

        source_id = positive(source.get("id"), "source ID")
        line_id = positive(
            source.get("statutory_line_id"),
            "statutory line ID",
        )
        income_line_id = positive(
            source.get("payroll_calculation_line_id"),
            "income line ID",
        )

        if source_id in source_ids:
            raise FinancialReconciliationError(
                "Duplicate source ID"
            )

        source_ids.add(source_id)

        pair = (line_id, income_line_id)
        if pair in source_pairs:
            raise FinancialReconciliationError(
                "Duplicate source binding"
            )

        source_pairs.add(pair)

        if line_id not in by_line:
            raise FinancialReconciliationError(
                "Orphan dated source"
            )

        target = by_line[line_id]

        if (
            source.get("company_id") != company_id
            or source.get("payroll_calculation_id")
            != target["calculation_id"]
        ):
            raise FinancialReconciliationError(
                "Source calculation mismatch"
            )

        start = source.get("income_effective_from")
        end = source.get("income_effective_to")

        if (
            start is None
            or end is None
            or not hasattr(start, "year")
            or not hasattr(end, "year")
            or start > end
        ):
            raise FinancialReconciliationError(
                "Invalid dated source interval"
            )

        source_amount = amount(source.get("source_amount"))

        target["sources"] += source_amount
        target["source_count"] += 1

        components[target["key"]][
            target["side"] + "_sources"
        ] += source_amount

    missing = sorted(
        identifier
        for identifier, row in by_line.items()
        if row["source_count"] == 0
    )

    mismatched = sorted(
        identifier
        for identifier, row in by_line.items()
        if row["sources"] != row["base"]
    )

    component_rows = []

    for (component, currency), totals in sorted(
        components.items()
    ):
        component_rows.append({
            "component": component,
            "currency": currency,
            "original_base": str(totals["historical_base"]),
            "replacement_base": str(totals["active_base"]),
            "base_delta": str(
                totals["active_base"] - totals["historical_base"]
            ),
            "original_tax": str(totals["historical_tax"]),
            "replacement_tax": str(totals["active_tax"]),
            "tax_delta": str(
                totals["active_tax"] - totals["historical_tax"]
            ),
            "original_sources": str(totals["historical_sources"]),
            "replacement_sources": str(totals["active_sources"]),
        })

    active_covered = {
        row["calculation_id"]
        for row in by_line.values()
        if row["side"] == "active"
    }

    historical_covered = {
        row["calculation_id"]
        for row in by_line.values()
        if row["side"] == "historical"
    }

    verified = (
        bool(component_rows)
        and not missing
        and not mismatched
        and active_covered == set(active)
        and historical_covered == set(historical)
    )

    return {
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "status": (
            "CORRECTION_COMPONENT_DELTA_RECONCILED"
            if verified else "NOT_VERIFIED"
        ),
        "components": component_rows,
        "missing_source_line_ids": missing,
        "mismatched_source_line_ids": mismatched,
        "active_calculation_ids": sorted(active),
        "historical_calculation_ids": sorted(historical),
        "historical_double_count_in_active_totals": False,
        "correction_chain_provenance": "INPUT_CHAIN_ONLY",
        "persisted_correction_delta_verified": False,
        "statutory_mapping_verified": False,
        "official_generation": "BLOCKED",
    }
