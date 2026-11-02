"""Persisted, read-only payroll correction delta reconciliation.

Uses actual payroll correction chains and attributed statutory sources.
Never modifies sealed statutory records or generates official reports.
"""

from sqlalchemy import select

from app.models.payroll import PayrollCalculationLine
from app.models.payroll_statutory import (
    PayrollStatutoryResult,
    PayrollStatutoryResultLine,
    PayrollStatutoryLineSource,
)
from app.services.payroll_regulatory_financial_reconciliation import (
    FinancialReconciliationError,
)
from app.services.payroll_regulatory_revision_chain_review import (
    review_persisted_revision_chain,
)
from app.services.payroll_regulatory_correction_delta import (
    positive,
    reconcile_correction_delta,
)


async def review_persisted_correction_delta(
    db,
    *,
    company_id,
    payroll_period_id,
):
    """Read authentic, version-scoped financial correction evidence."""

    positive(company_id, "company")
    positive(payroll_period_id, "payroll period")

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
            "persisted_correction_delta_verified": False,
            "official_generation": "BLOCKED",
        }

    historical_ids = chain["historical_calculation_ids"]
    active_ids = chain["active_calculation_ids"]

    if not historical_ids:
        return {
            "company_id": company_id,
            "payroll_period_id": payroll_period_id,
            "status": "NOT_VERIFIED",
            "reason": "NO_PERSISTED_REPLACEMENT_CORRECTION",
            "active_calculation_ids": active_ids,
            "historical_calculation_ids": [],
            "persisted_correction_delta_verified": False,
            "official_generation": "BLOCKED",
        }

    calculation_ids = active_ids + historical_ids

    # Composite company and parent statutory-result joins are
    # essential to prevent cross-company or cross-calculation evidence.
    rows = (
        await db.execute(
            select(
                PayrollStatutoryResultLine.id.label("id"),
                PayrollStatutoryResultLine.company_id.label(
                    "company_id"
                ),
                PayrollStatutoryResult.payroll_calculation_id.label(
                    "payroll_calculation_id"
                ),
                PayrollStatutoryResultLine.component.label(
                    "component"
                ),
                PayrollStatutoryResultLine.currency_code.label(
                    "currency_code"
                ),
                PayrollStatutoryResultLine.base_amount.label(
                    "base_amount"
                ),
                PayrollStatutoryResultLine.amount.label("amount"),
            )
            .join(
                PayrollStatutoryResult,
                (
                    PayrollStatutoryResult.company_id
                    == PayrollStatutoryResultLine.company_id
                )
                & (
                    PayrollStatutoryResult.id
                    == PayrollStatutoryResultLine.payroll_statutory_result_id
                ),
            )
            .where(
                PayrollStatutoryResult.company_id == company_id,
                PayrollStatutoryResult.payroll_calculation_id.in_(
                    calculation_ids
                ),
            )
        )
    ).mappings().all()

    line_ids = [row["id"] for row in rows]

    sources = []

    if line_ids:
        sources = (
            await db.execute(
                select(
                    PayrollStatutoryLineSource.id.label("id"),
                    PayrollStatutoryLineSource.company_id.label(
                        "company_id"
                    ),
                    PayrollStatutoryLineSource.statutory_line_id.label(
                        "statutory_line_id"
                    ),
                    PayrollStatutoryLineSource.payroll_calculation_line_id.label(
                        "payroll_calculation_line_id"
                    ),
                    PayrollCalculationLine.payroll_calculation_id.label(
                        "payroll_calculation_id"
                    ),
                    PayrollStatutoryLineSource.source_amount.label(
                        "source_amount"
                    ),
                    PayrollStatutoryLineSource.income_effective_from.label(
                        "income_effective_from"
                    ),
                    PayrollStatutoryLineSource.income_effective_to.label(
                        "income_effective_to"
                    ),
                )
                .join(
                    PayrollCalculationLine,
                    (
                        PayrollCalculationLine.company_id
                        == PayrollStatutoryLineSource.company_id
                    )
                    & (
                        PayrollCalculationLine.id
                        == PayrollStatutoryLineSource.payroll_calculation_line_id
                    ),
                )
                .where(
                    PayrollStatutoryLineSource.company_id == company_id,
                    PayrollStatutoryLineSource.statutory_line_id.in_(
                        line_ids
                    ),
                )
            )
        ).mappings().all()

    evidence = reconcile_correction_delta(
        chain,
        [dict(row) for row in rows],
        [dict(row) for row in sources],
        company_id=company_id,
        payroll_period_id=payroll_period_id,
    )

    result = dict(evidence)

    verified = (
        result["status"]
        == "CORRECTION_COMPONENT_DELTA_RECONCILED"
        and bool(result["components"])
        and not result["missing_source_line_ids"]
        and not result["mismatched_source_line_ids"]
    )

    result["data_origin"] = "PERSISTED_POSTGRESQL"
    result["correction_chain_provenance"] = (
        "PERSISTED_POSTGRESQL"
    )
    result["persisted_correction_delta_verified"] = verified

    if not verified:
        result["status"] = "NOT_VERIFIED"

    # A successful financial delta does not approve statutory mappings.
    result["statutory_mapping_verified"] = False
    result["official_generation"] = "BLOCKED"

    return result
