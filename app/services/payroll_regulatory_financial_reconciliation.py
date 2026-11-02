"""Independent financial-source reconciliation for internal D1/4DF review.

Only evidence with explicit monetary fields can be verified.
Absent or unsupported evidence remains NOT_VERIFIED.

This module never generates or approves official statutory rows.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import inspect, text


class FinancialReconciliationError(ValueError):
    pass


def positive_id(value, field):
    if type(value) is not int or value <= 0:
        raise FinancialReconciliationError(
            "Invalid " + field
        )
    return value


def money(value):
    """Convert exact decimal values, rejecting floats and booleans."""

    if isinstance(value, bool) or isinstance(value, float):
        raise FinancialReconciliationError(
            "Financial evidence must not use binary floats"
        )

    if not isinstance(value, (int, str, Decimal)):
        raise FinancialReconciliationError(
            "Invalid financial amount type"
        )

    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise FinancialReconciliationError(
            "Invalid financial amount"
        ) from exc

    if not amount.is_finite():
        raise FinancialReconciliationError(
            "Non-finite financial amount"
        )

    if amount.as_tuple().exponent < -2:
        raise FinancialReconciliationError(
            "Financial amount has excess precision"
        )

    return amount


def reconcile_lines(lines, *, company_id, payroll_period_id):
    """Independently compare line amounts against bound source amounts.

    Input schema:
      line_id, company_id, payroll_period_id,
      reported_amount, sources: [{source_id, source_amount}]

    No empty source set is treated as verified.
    """

    positive_id(company_id, "company")
    positive_id(payroll_period_id, "payroll period")

    if not isinstance(lines, list):
        raise FinancialReconciliationError(
            "Financial lines must be a list"
        )

    seen_lines = set()
    verified = 0
    blocked = 0
    differences = []
    total_reported = Decimal("0.00")
    total_sources = Decimal("0.00")

    for line in lines:
        if not isinstance(line, dict):
            raise FinancialReconciliationError(
                "Invalid financial line"
            )

        line_id = positive_id(line.get("line_id"), "line")

        if line_id in seen_lines:
            raise FinancialReconciliationError(
                "Duplicate financial line"
            )

        seen_lines.add(line_id)

        if line.get("company_id") != company_id:
            raise FinancialReconciliationError(
                "Cross-company financial line"
            )

        if line.get("payroll_period_id") != payroll_period_id:
            raise FinancialReconciliationError(
                "Cross-period financial line"
            )

        reported = money(line.get("reported_amount"))

        if reported < 0:
            raise FinancialReconciliationError(
                "Negative reported amount requires explicit "
                "correction evidence"
            )

        sources = line.get("sources")

        if not isinstance(sources, list):
            raise FinancialReconciliationError(
                "Financial source list required"
            )

        source_ids = set()
        allocated = Decimal("0.00")

        if not sources:
            blocked += 1
            differences.append({
                "line_id": line_id,
                "status": "MISSING_SOURCES",
            })
            total_reported += reported
            continue

        for source in sources:
            if not isinstance(source, dict):
                raise FinancialReconciliationError(
                    "Invalid source binding"
                )

            source_id = positive_id(
                source.get("source_id"),
                "source",
            )

            if source_id in source_ids:
                raise FinancialReconciliationError(
                    "Duplicate financial source binding"
                )

            source_ids.add(source_id)

            if (
                source.get("company_id", company_id)
                != company_id
            ):
                raise FinancialReconciliationError(
                    "Cross-company source binding"
                )

            amount = money(source.get("source_amount"))

            if amount < 0:
                raise FinancialReconciliationError(
                    "Negative source requires explicit "
                    "correction evidence"
                )

            allocated += amount

        total_reported += reported
        total_sources += allocated

        if allocated != reported:
            blocked += 1
            differences.append({
                "line_id": line_id,
                "status": "AMOUNT_MISMATCH",
                "reported_amount": str(reported),
                "source_amount": str(allocated),
                "difference": str(reported - allocated),
            })
        else:
            verified += 1

    return {
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "line_count": len(lines),
        "verified_line_count": verified,
        "blocked_line_count": blocked,
        "reported_total": str(total_reported),
        "source_total": str(total_sources),
        "differences": differences,
        "status": (
            "NOT_VERIFIED" if not lines or blocked
            else "FINANCIAL_LINES_RECONCILED"
        ),
        "statutory_mapping_verified": False,
        "official_generation": "BLOCKED",
    }


async def inspect_persisted_financial_sources(
    db,
    *,
    company_id,
    payroll_period_id,
):
    """Inspect actual statutory-source schema and independent counts.

    Never infer statutory amount columns or event mappings.
    """

    positive_id(company_id, "company")
    positive_id(payroll_period_id, "payroll period")

    def inspect_schema(sync_session):
        inspector = inspect(sync_session.connection())

        required = (
            "payroll_statutory_result_lines",
            "payroll_statutory_line_sources",
            "payroll_calculation_lines",
        )

        tables = set(inspector.get_table_names())

        return {
            name: (
                {
                    column["name"]
                    for column in inspector.get_columns(name)
                }
                if name in tables else None
            )
            for name in required
        }

    schema = await db.run_sync(inspect_schema)

    missing = [
        name for name, columns in schema.items()
        if columns is None
    ]

    columns_summary = {
        name: sorted(columns) if columns is not None else []
        for name, columns in schema.items()
    }

    if missing:
        return {
            "company_id": company_id,
            "payroll_period_id": payroll_period_id,
            "status": "NOT_VERIFIED",
            "reason": "MISSING_SOURCE_TABLES",
            "missing_tables": missing,
            "columns": columns_summary,
            "official_generation": "BLOCKED",
        }

    # Verify real tenant-period identity. Do not assume
    # payroll period IDs from a different tenant are valid.
    period = (
        await db.execute(
            text("""
                SELECT id
                FROM payroll_periods
                WHERE id = :period_id
                  AND company_id = :company_id
            """),
            {
                "period_id": payroll_period_id,
                "company_id": company_id,
            },
        )
    ).scalar_one_or_none()

    if period is None:
        raise FinancialReconciliationError(
            "Payroll period does not belong to company"
        )

    # This inspection is evidence discovery, not a claim
    # that regulatory financial amounts are reconciled.
    return {
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "status": "NOT_VERIFIED",
        "reason": "INDEPENDENT_AMOUNT_BINDING_REQUIRED",
        "missing_tables": [],
        "columns": columns_summary,
        "official_generation": "BLOCKED",
    }
