"""Reconcile frozen payroll/statutory inputs for report preparation.

This provides verified source totals, NOT statutory form mapping.
No SQL writes, statutory code assumptions, or implicit submission.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

COMPONENTS = (
    "personal_income_tax",
    "military_levy",
    "unified_social_contribution",
)

ZERO = Decimal("0.00")


class RegulatorySourceReconciliationError(ValueError):
    pass


@dataclass(frozen=True)
class RegulatorySourceTotals:
    company_id: int
    payroll_period_id: int
    report_kind: str
    source_count: int
    gross: Decimal
    net: Decimal
    employee_withholding: Decimal
    employer_contribution: Decimal
    pit: Decimal
    military_levy: Decimal
    usc: Decimal
    source_ids: tuple[int, ...]


def amount(value, name):
    try:
        if isinstance(value, bool) or value is None:
            raise ValueError
        result = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        raise RegulatorySourceReconciliationError(
            f"Invalid {name}"
        )

    if not result.is_finite():
        raise RegulatorySourceReconciliationError(
            f"Non-finite {name}"
        )

    if result.as_tuple().exponent < -2:
        raise RegulatorySourceReconciliationError(
            f"Invalid precision for {name}"
        )

    return result


def reconcile_regulatory_sources(readiness):
    """Reject incomplete or inconsistent underlying payroll data."""
    if not isinstance(readiness, dict):
        raise RegulatorySourceReconciliationError(
            "Readiness data required"
        )

    rows = readiness.get("calculation_sources")
    if not isinstance(rows, list):
        raise RegulatorySourceReconciliationError(
            "Calculation sources must be a list"
        )

    company_id = readiness["company_id"]
    period_id = readiness["payroll_period_id"]
    kind = readiness["report_kind"]

    total_gross = ZERO
    total_net = ZERO
    total_withholding = ZERO
    total_employer = ZERO
    totals = {component: ZERO for component in COMPONENTS}

    seen = set()
    currencies = set()

    for row in rows:
        if row.get("issues"):
            raise RegulatorySourceReconciliationError(
                "Unresolved payroll source issues"
            )

        calculation_id = row.get("payroll_calculation_id")
        if type(calculation_id) is not int or calculation_id <= 0:
            raise RegulatorySourceReconciliationError(
                "Invalid payroll calculation identifier"
            )

        if calculation_id in seen:
            raise RegulatorySourceReconciliationError(
                "Duplicate current payroll calculation"
            )
        seen.add(calculation_id)

        if (
            row.get("employee_id") is None
            or row.get("employment_contract_id") is None
            or not row.get("tax_number")
        ):
            raise RegulatorySourceReconciliationError(
                "Missing employee, contract or verified tax identifier"
            )

        currency = row.get("currency_code")
        if currency != "UAH":
            raise RegulatorySourceReconciliationError(
                "Statutory source currency must be UAH"
            )
        currencies.add(currency)

        gross = amount(row.get("gross_amount"), "gross")
        net = amount(row.get("net_amount"), "net")
        withheld = amount(
            row.get("employee_withholding_amount"),
            "employee withholding",
        )
        employer = amount(
            row.get("employer_contribution_amount"),
            "employer contribution",
        )

        if min(gross, net, withheld, employer) < ZERO:
            raise RegulatorySourceReconciliationError(
                "Negative statutory source totals"
            )

        if gross - withheld != net:
            raise RegulatorySourceReconciliationError(
                "Gross, withholding and net do not reconcile"
            )

        lines = row.get("components")
        if not isinstance(lines, list) or not lines:
            raise RegulatorySourceReconciliationError(
                "Frozen statutory component lines are missing"
            )

        row_totals = {
            component: ZERO for component in COMPONENTS
        }
        line_ids = set()

        for line in lines:
            component = line.get("component")
            if component not in COMPONENTS:
                raise RegulatorySourceReconciliationError(
                    "Unsupported statutory component"
                )

            line_id = line.get("source_line_id")
            if type(line_id) is not int or line_id <= 0:
                raise RegulatorySourceReconciliationError(
                    "Missing frozen statutory line identifier"
                )
            if line_id in line_ids:
                raise RegulatorySourceReconciliationError(
                    "Duplicate statutory line"
                )
            line_ids.add(line_id)

            if line.get("currency_code") != currency:
                raise RegulatorySourceReconciliationError(
                    "Statutory line currency mismatch"
                )

            base = amount(line.get("base_amount"), "base")
            rate = Decimal(str(line.get("rate")))
            value = amount(line.get("amount"), "component")

            if (
                not rate.is_finite()
                or rate < 0
                or rate > 1
                or base < ZERO
                or value < ZERO
            ):
                raise RegulatorySourceReconciliationError(
                    "Invalid component base, rate or value"
                )

            row_totals[component] += value

        tax_total = (
            row_totals["personal_income_tax"]
            + row_totals["military_levy"]
        )

        if tax_total != withheld:
            raise RegulatorySourceReconciliationError(
                "PIT and levy do not reconcile to withholding"
            )

        if (
            row_totals["unified_social_contribution"]
            != employer
        ):
            raise RegulatorySourceReconciliationError(
                "USC does not reconcile to employer contribution"
            )

        total_gross += gross
        total_net += net
        total_withholding += withheld
        total_employer += employer

        for component in COMPONENTS:
            totals[component] += row_totals[component]

    return RegulatorySourceTotals(
        company_id=company_id,
        payroll_period_id=period_id,
        report_kind=kind,
        source_count=len(seen),
        gross=total_gross,
        net=total_net,
        employee_withholding=total_withholding,
        employer_contribution=total_employer,
        pit=totals["personal_income_tax"],
        military_levy=totals["military_levy"],
        usc=totals["unified_social_contribution"],
        source_ids=tuple(sorted(seen)),
    )
