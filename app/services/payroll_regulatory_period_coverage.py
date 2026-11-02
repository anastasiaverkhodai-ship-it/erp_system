"""Fail-closed payroll-period financial source coverage.

This is evidence review, not official D1 or 4DF generation.
Payroll revisions require explicit resolution before period closure.
"""

from collections import Counter
from decimal import Decimal

from sqlalchemy import select

from app.models.payroll import (
    PayrollCalculation,
    PayrollPeriod,
)
from app.models.payroll_statutory import PayrollStatutoryResult
from app.services.payroll_regulatory_financial_reconciliation import (
    FinancialReconciliationError,
    positive_id,
)
from app.services.payroll_regulatory_persisted_allocation import (
    reconcile_persisted_allocations,
)


def evaluate_period_coverage(
    calculations,
    statutory_results,
    allocation,
    *,
    company_id,
    payroll_period_id,
):
    positive_id(company_id, "company")
    positive_id(payroll_period_id, "period")

    if not isinstance(calculations, list):
        raise FinancialReconciliationError("Invalid calculations")

    if not isinstance(statutory_results, list):
        raise FinancialReconciliationError("Invalid statutory results")

    if not isinstance(allocation, dict):
        raise FinancialReconciliationError("Invalid allocation")

    if (
        allocation.get("company_id") != company_id
        or allocation.get("payroll_period_id") != payroll_period_id
    ):
        raise FinancialReconciliationError(
            "Cross-company or cross-period allocation"
        )

    calculation_ids = set()
    revision_groups = Counter()

    for row in calculations:
        identifier = positive_id(row.get("id"), "calculation")

        if identifier in calculation_ids:
            raise FinancialReconciliationError(
                "Duplicate payroll calculation"
            )

        if (
            row.get("company_id") != company_id
            or row.get("payroll_period_id") != payroll_period_id
        ):
            raise FinancialReconciliationError(
                "Cross-scope payroll calculation"
            )

        positive_id(row.get("revision"), "revision")
        positive_id(row.get("employment_contract_id"), "contract")

        calculation_ids.add(identifier)
        revision_groups[row["employment_contract_id"]] += 1

    result_counts = Counter()

    for row in statutory_results:
        identifier = positive_id(
            row.get("payroll_calculation_id"),
            "statutory calculation",
        )

        if row.get("company_id") != company_id:
            raise FinancialReconciliationError(
                "Cross-company statutory result"
            )

        if identifier not in calculation_ids:
            raise FinancialReconciliationError(
                "Orphan or out-of-period statutory result"
            )

        result_counts[identifier] += 1

    missing = sorted(
        identifier for identifier in calculation_ids
        if result_counts[identifier] == 0
    )

    duplicate_results = sorted(
        identifier for identifier, count in result_counts.items()
        if count > 1
    )

    ambiguous_contracts = sorted(
        identifier for identifier, count in revision_groups.items()
        if count > 1
    )

    financial_ok = (
        allocation.get("status") == "FINANCIAL_LINES_RECONCILED"
        and allocation.get("data_origin") == "PERSISTED_POSTGRESQL"
        and type(allocation.get("line_count")) is int
        and type(allocation.get("verified_lines")) is int
        and allocation["line_count"] > 0
        and allocation["line_count"] == allocation["verified_lines"]
        and allocation.get("reported_total")
        == allocation.get("allocated_total")
        and allocation.get("official_generation") == "BLOCKED"
        and allocation.get("statutory_mapping_verified") is False
    )

    complete = (
        bool(calculation_ids)
        and not missing
        and not duplicate_results
        and not ambiguous_contracts
        and financial_ok
        and len(statutory_results) == len(calculation_ids)
    )

    return {
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "status": (
            "PERIOD_FINANCIAL_COVERAGE_VERIFIED"
            if complete else "NOT_VERIFIED"
        ),
        "calculation_count": len(calculation_ids),
        "statutory_result_count": len(statutory_results),
        "missing_calculation_ids": missing,
        "duplicate_result_calculation_ids": duplicate_results,
        "ambiguous_revision_contract_ids": ambiguous_contracts,
        "financial_allocation_verified": financial_ok,
        "statutory_mapping_verified": False,
        "official_generation": "BLOCKED",
    }


async def review_persisted_period_coverage(
    db,
    *,
    company_id,
    payroll_period_id,
):
    positive_id(company_id, "company")
    positive_id(payroll_period_id, "period")

    period = await db.scalar(
        select(PayrollPeriod.id).where(
            PayrollPeriod.company_id == company_id,
            PayrollPeriod.id == payroll_period_id,
        )
    )

    if period is None:
        raise FinancialReconciliationError(
            "Payroll period does not belong to company"
        )

    calculations = (
        await db.execute(
            select(
                PayrollCalculation.id,
                PayrollCalculation.company_id,
                PayrollCalculation.payroll_period_id,
                PayrollCalculation.employment_contract_id,
                PayrollCalculation.revision,
            ).where(
                PayrollCalculation.company_id == company_id,
                PayrollCalculation.payroll_period_id == payroll_period_id,
            )
        )
    ).mappings().all()

    ids = [row["id"] for row in calculations]

    statutory_results = []

    if ids:
        statutory_results = (
            await db.execute(
                select(
                    PayrollStatutoryResult.company_id,
                    PayrollStatutoryResult.payroll_calculation_id,
                ).where(
                    PayrollStatutoryResult.company_id == company_id,
                    PayrollStatutoryResult.payroll_calculation_id.in_(ids),
                )
            )
        ).mappings().all()

    allocation = await reconcile_persisted_allocations(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
    )

    output = evaluate_period_coverage(
        [dict(row) for row in calculations],
        [dict(row) for row in statutory_results],
        allocation,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
    )

    output["data_origin"] = "PERSISTED_POSTGRESQL"
    output["allocation_status"] = allocation["status"]

    return output
