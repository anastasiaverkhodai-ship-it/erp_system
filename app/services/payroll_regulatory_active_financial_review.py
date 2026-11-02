"""Active-revision-only statutory financial review.

This review excludes replaced calculations from active totals while
preserving historical evidence. Statutory filing remains blocked.
"""

from decimal import Decimal

from sqlalchemy import select

from app.models.payroll import PayrollCalculation
from app.models.payroll_statutory import (
    PayrollStatutoryResult,
    PayrollStatutoryResultLine,
    PayrollStatutoryLineSource,
)
from app.services.payroll_regulatory_financial_reconciliation import (
    FinancialReconciliationError,
    money,
    positive_id,
)
from app.services.payroll_regulatory_revision_chain_review import (
    review_persisted_revision_chain,
)


def reconcile_active_revisions(
    chain,
    lines,
    sources,
    *,
    company_id,
    payroll_period_id,
):
    """Reconcile active tax bases without counting historical revisions."""

    positive_id(company_id, "company")
    positive_id(payroll_period_id, "period")

    if not isinstance(chain, dict):
        raise FinancialReconciliationError("Invalid revision chain")

    if not isinstance(lines, list) or not isinstance(sources, list):
        raise FinancialReconciliationError("Invalid evidence collections")

    if (
        chain.get("company_id") != company_id
        or chain.get("payroll_period_id") != payroll_period_id
        or chain.get("status") != "REVISION_CHAIN_VERIFIED"
        or chain.get("official_generation") != "BLOCKED"
        or chain.get("data_origin") != "PERSISTED_POSTGRESQL"
    ):
        raise FinancialReconciliationError(
            "Unverified revision chain or mismatched scope"
        )

    active = chain.get("active_calculation_ids")
    historical = chain.get("historical_calculation_ids")

    if (
        not isinstance(active, list)
        or not active
        or not isinstance(historical, list)
        or any(type(i) is not int or i <= 0 for i in active + historical)
        or len(set(active + historical)) != len(active + historical)
    ):
        raise FinancialReconciliationError(
            "Invalid active/historical revision partition"
        )

    active_ids = set(active)
    historical_ids = set(historical)
    all_ids = active_ids | historical_ids

    if (
        chain.get("calculation_count") != len(all_ids)
        or chain.get("correction_count", 0) < len(historical_ids)
    ):
        raise FinancialReconciliationError(
            "Incomplete revision partition"
        )

    indexed_lines = {}

    for row in lines:
        if not isinstance(row, dict):
            raise FinancialReconciliationError("Invalid statutory line")

        identifier = positive_id(row.get("id"), "statutory line")
        calculation_id = positive_id(
            row.get("payroll_calculation_id"),
            "source calculation",
        )

        if identifier in indexed_lines:
            raise FinancialReconciliationError(
                "Duplicate statutory line"
            )

        if (
            row.get("company_id") != company_id
            or calculation_id not in all_ids
        ):
            raise FinancialReconciliationError(
                "Cross-company or unknown calculation"
            )

        indexed_lines[identifier] = row

    grouped = {}
    source_ids = set()
    source_pairs = set()

    for source in sources:
        if not isinstance(source, dict):
            raise FinancialReconciliationError("Invalid dated source")

        source_id = positive_id(source.get("id"), "source")
        statutory_line_id = positive_id(
            source.get("statutory_line_id"), "statutory line"
        )
        payroll_line_id = positive_id(
            source.get("payroll_calculation_line_id"),
            "payroll calculation line",
        )

        if source_id in source_ids:
            raise FinancialReconciliationError("Duplicate source ID")

        source_ids.add(source_id)

        pair = (statutory_line_id, payroll_line_id)
        if pair in source_pairs:
            raise FinancialReconciliationError(
                "Duplicate source allocation binding"
            )
        source_pairs.add(pair)

        if statutory_line_id not in indexed_lines:
            raise FinancialReconciliationError(
                "Orphan statutory allocation"
            )

        if source.get("company_id") != company_id:
            raise FinancialReconciliationError(
                "Cross-company allocation"
            )

        if (
            source.get("payroll_calculation_id")
            != indexed_lines[statutory_line_id]["payroll_calculation_id"]
        ):
            raise FinancialReconciliationError(
                "Source references a different calculation"
            )

        first = source.get("income_effective_from")
        last = source.get("income_effective_to")

        if (
            first is None or last is None
            or not hasattr(first, "year")
            or not hasattr(last, "year")
            or first > last
        ):
            raise FinancialReconciliationError(
                "Invalid source income dates"
            )

        grouped.setdefault(statutory_line_id, []).append(source)

    active_base = Decimal("0.00")
    active_allocated = Decimal("0.00")
    historical_base = Decimal("0.00")
    historical_allocated = Decimal("0.00")
    active_tax = Decimal("0.00")
    errors = []
    active_count = 0
    historical_count = 0

    for line_id, line in sorted(indexed_lines.items()):
        base = money(line.get("base_amount"))
        tax = money(line.get("amount"))
        links = grouped.get(line_id, [])
        allocated = sum(
            (money(s.get("source_amount")) for s in links),
            Decimal("0.00"),
        )

        calculation_id = line["payroll_calculation_id"]

        if calculation_id in active_ids:
            active_count += 1
            active_base += base
            active_allocated += allocated
            active_tax += tax

            if not links:
                errors.append({
                    "line_id": line_id,
                    "reason": "MISSING_ACTIVE_SOURCES",
                })
            elif allocated != base:
                errors.append({
                    "line_id": line_id,
                    "reason": "ACTIVE_BASE_MISMATCH",
                    "difference": str(base - allocated),
                })
        else:
            historical_count += 1
            historical_base += base
            historical_allocated += allocated

    complete = (
        active_count > 0
        and not errors
        and active_base == active_allocated
        and active_ids <= {
            row["payroll_calculation_id"]
            for row in indexed_lines.values()
        }
    )

    return {
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "status": (
            "ACTIVE_FINANCIAL_RECONCILED"
            if complete else "NOT_VERIFIED"
        ),
        "active_calculation_count": len(active_ids),
        "historical_calculation_count": len(historical_ids),
        "active_statutory_line_count": active_count,
        "historical_statutory_line_count": historical_count,
        "active_base_total": str(active_base),
        "active_allocated_total": str(active_allocated),
        "active_statutory_tax_total": str(active_tax),
        "excluded_historical_base_total": str(historical_base),
        "excluded_historical_allocated_total": str(
            historical_allocated
        ),
        "errors": errors,
        "correction_delta_verified": False,
        "statutory_mapping_verified": False,
        "official_generation": "BLOCKED",
    }


async def review_persisted_active_financials(
    db,
    *,
    company_id,
    payroll_period_id,
):
    """Read persisted revision chains and their dated source allocations."""

    chain = await review_persisted_revision_chain(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
    )

    if chain.get("status") != "REVISION_CHAIN_VERIFIED":
        return {
            "company_id": company_id,
            "payroll_period_id": payroll_period_id,
            "status": "NOT_VERIFIED",
            "reason": "REVISION_CHAIN_NOT_VERIFIED",
            "official_generation": "BLOCKED",
        }

    ids = (
        chain["active_calculation_ids"]
        + chain["historical_calculation_ids"]
    )

    records = (
        await db.execute(
            select(
                PayrollStatutoryResultLine.id.label("id"),
                PayrollStatutoryResultLine.company_id.label(
                    "company_id"
                ),
                PayrollStatutoryResult.payroll_calculation_id.label(
                    "payroll_calculation_id"
                ),
                PayrollStatutoryResultLine.base_amount.label(
                    "base_amount"
                ),
                PayrollStatutoryResultLine.amount.label("amount"),
            )
            .join(
                PayrollStatutoryResult,
                (
                    PayrollStatutoryResultLine.company_id
                    == PayrollStatutoryResult.company_id
                ) & (
                    PayrollStatutoryResultLine.payroll_statutory_result_id
                    == PayrollStatutoryResult.id
                ),
            )
            .where(
                PayrollStatutoryResult.company_id == company_id,
                PayrollStatutoryResult.payroll_calculation_id.in_(ids),
            )
        )
    ).mappings().all()

    statutory_line_ids = [row["id"] for row in records]
    links = []

    if statutory_line_ids:
        from app.models.payroll import PayrollCalculationLine

        links = (
            await db.execute(
                select(
                    PayrollStatutoryLineSource.id,
                    PayrollStatutoryLineSource.company_id,
                    PayrollStatutoryLineSource.statutory_line_id,
                    PayrollStatutoryLineSource.payroll_calculation_line_id,
                    PayrollStatutoryLineSource.source_amount,
                    PayrollStatutoryLineSource.income_effective_from,
                    PayrollStatutoryLineSource.income_effective_to,
                    PayrollCalculationLine.payroll_calculation_id,
                )
                .join(
                    PayrollCalculationLine,
                    (
                        PayrollCalculationLine.company_id
                        == PayrollStatutoryLineSource.company_id
                    ) & (
                        PayrollCalculationLine.id
                        == PayrollStatutoryLineSource.payroll_calculation_line_id
                    ),
                )
                .where(
                    PayrollStatutoryLineSource.company_id == company_id,
                    PayrollStatutoryLineSource.statutory_line_id.in_(
                        statutory_line_ids
                    ),
                )
            )
        ).mappings().all()

    result = reconcile_active_revisions(
        chain,
        [dict(row) for row in records],
        [dict(row) for row in links],
        company_id=company_id,
        payroll_period_id=payroll_period_id,
    )
    result["data_origin"] = "PERSISTED_POSTGRESQL"
    return result
