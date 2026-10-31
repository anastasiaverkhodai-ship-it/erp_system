"""Verified internal D1/USC evidence projection.

This produces financial review data only. It does not determine
statutory D1 categories, approve mappings, or create filing-ready rows.
"""

from collections import defaultdict
from decimal import Decimal

from app.services.payroll_regulatory_persisted_source_verifier import (
    verify_persisted_payroll_sources,
)
from app.services.payroll_regulatory_readiness_service import (
    payroll_regulatory_readiness,
)
from app.services.payroll_regulatory_source_reconciliation import (
    reconcile_regulatory_sources,
)


class D1ReviewProjectionError(ValueError):
    pass


ZERO = Decimal("0.00")
USC = "unified_social_contribution"


def checked_amount(value, field):
    if value is None or isinstance(value, bool):
        raise D1ReviewProjectionError(f"Invalid {field}")

    try:
        amount = Decimal(str(value))
    except (ValueError, ArithmeticError, TypeError) as exc:
        raise D1ReviewProjectionError(f"Invalid {field}") from exc

    if not amount.is_finite():
        raise D1ReviewProjectionError(f"Invalid {field}")

    if amount.as_tuple().exponent < -2:
        raise D1ReviewProjectionError(
            f"Invalid monetary precision: {field}"
        )

    return amount


def make_d1_review_projection(readiness):
    """Make deterministic, traceable, review-only USC source rows."""

    if not isinstance(readiness, dict):
        raise D1ReviewProjectionError("Readiness payload required")

    if readiness.get("report_kind") != "d1":
        raise D1ReviewProjectionError("D1 readiness required")

    company_id = readiness.get("company_id")
    period_id = readiness.get("payroll_period_id")

    if (
        type(company_id) is not int
        or company_id <= 0
        or type(period_id) is not int
        or period_id <= 0
    ):
        raise D1ReviewProjectionError("Invalid payroll scope")

    sources = readiness.get("calculation_sources")

    if not isinstance(sources, list) or not sources:
        raise D1ReviewProjectionError("D1 payroll sources required")

    # Re-run the established statutory reconciliation contract.
    totals = reconcile_regulatory_sources(readiness)

    rows = []
    used_calculations = set()
    used_lines = set()

    for source in sources:
        calculation_id = source["payroll_calculation_id"]

        if calculation_id in used_calculations:
            raise D1ReviewProjectionError(
                "Duplicated D1 payroll calculation"
            )

        used_calculations.add(calculation_id)

        usc_lines = [
            line
            for line in source["components"]
            if line["component"] == USC
        ]

        if not usc_lines:
            raise D1ReviewProjectionError(
                "Missing frozen USC evidence for D1"
            )

        source_usc = ZERO
        evidence = []

        for line in usc_lines:
            line_id = line["source_line_id"]

            if line_id in used_lines:
                raise D1ReviewProjectionError(
                    "Duplicated D1 statutory line evidence"
                )

            used_lines.add(line_id)

            amount = checked_amount(line["amount"], "USC amount")
            base = checked_amount(line["base_amount"], "USC base")

            if amount < ZERO or base < ZERO:
                raise D1ReviewProjectionError(
                    "Negative D1 financial evidence"
                )

            source_usc += amount

            evidence.append({
                "statutory_line_id": line_id,
                "component": USC,
                "base_amount": str(base),
                "amount": str(amount),
                "currency_code": line["currency_code"],
            })

        employer = checked_amount(
            source["employer_contribution_amount"],
            "employer contribution",
        )

        if source_usc != employer:
            raise D1ReviewProjectionError(
                "D1 USC evidence does not reconcile"
            )

        rows.append({
            "company_id": company_id,
            "payroll_period_id": period_id,
            "employee_id": source["employee_id"],
            "employment_contract_id": source[
                "employment_contract_id"
            ],
            "payroll_calculation_id": calculation_id,
            "calculation_revision": source["revision"],
            "currency_code": source["currency_code"],
            "gross_amount": str(
                checked_amount(
                    source["gross_amount"], "gross amount"
                )
            ),
            "net_amount": str(
                checked_amount(
                    source["net_amount"], "net amount"
                )
            ),
            "employee_withholding_amount": str(
                checked_amount(
                    source["employee_withholding_amount"],
                    "employee withholding",
                )
            ),
            "employer_contribution_amount": str(employer),
            "usc_evidence": evidence,
            "review_only": True,
        })

    rows.sort(
        key=lambda row: (
            row["employee_id"],
            row["employment_contract_id"],
            row["payroll_calculation_id"],
        )
    )

    total_usc = sum(
        (
            Decimal(row["employer_contribution_amount"])
            for row in rows
        ),
        ZERO,
    )

    if total_usc != totals.usc:
        raise D1ReviewProjectionError(
            "D1 projection does not reconcile with source totals"
        )

    if len(rows) != totals.source_count:
        raise D1ReviewProjectionError(
            "D1 calculation coverage mismatch"
        )

    return {
        "company_id": company_id,
        "payroll_period_id": period_id,
        "report_kind": "d1",
        "review_only": True,
        "official_statutory_generation": "BLOCKED",
        "approved_normative_mapping": False,
        "source_count": len(rows),
        "gross_total": str(totals.gross),
        "employee_withholding_total": str(
            totals.employee_withholding
        ),
        "employer_contribution_total": str(total_usc),
        "rows": rows,
    }


async def build_verified_d1_review(
    db,
    *,
    company_id,
    payroll_period_id,
):
    """Load actual PostgreSQL sources, verify them, then project D1."""

    readiness = await payroll_regulatory_readiness(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        report_kind="d1",
    )

    if readiness.get("report_kind") != "d1":
        raise D1ReviewProjectionError("Wrong report kind")

    await verify_persisted_payroll_sources(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        readiness=readiness,
    )

    return make_d1_review_projection(readiness)
