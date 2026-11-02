"""Read-only financial verification of persisted regulatory allocations.

This service uses the actual database foreign-key relationships, not
guessed joins. A source allocation is verified only when its report
line, payroll calculation line, company and period can be proved.

Ambiguous amount fields, missing links and absent evidence are
NOT_VERIFIED. This does not approve statutory form mappings.
"""

from decimal import Decimal
from sqlalchemy import (
    MetaData,
    Table,
    inspect,
    select,
)

from app.services.payroll_regulatory_financial_reconciliation import (
    FinancialReconciliationError,
    money,
    positive_id,
)


TABLES = (
    "payroll_periods",
    "payroll_statutory_results",
    "payroll_statutory_result_lines",
    "payroll_statutory_line_sources",
    "payroll_calculations",
    "payroll_calculation_lines",
)

AMOUNT_CANDIDATES = (
    "reported_amount",
    "amount",
    "line_amount",
)

MAX_ROWS = 10000


def blocked(company_id, period_id, reason, **details):
    return {
        "company_id": company_id,
        "payroll_period_id": period_id,
        "status": "NOT_VERIFIED",
        "reason": reason,
        "verified_lines": 0,
        "official_generation": "BLOCKED",
        **details,
    }


def one_foreign_key(schema, child, parent):
    """Select a single, unambiguous simple FK between actual tables."""

    matches = []

    for fk in schema[child]["foreign_keys"]:
        if fk.get("referred_table") != parent:
            continue

        local = fk.get("constrained_columns") or []
        remote = fk.get("referred_columns") or []

        if len(local) != len(remote) or not local:
            continue

        if len(local) == 1 and len(remote) == 1:
            matches.append((local[0], remote[0]))

    if len(matches) != 1:
        return None

    return matches[0]


def verify_allocations(
    reported_lines,
    source_rows,
    calculation_lines,
    *,
    company_id,
    payroll_period_id,
):
    """Independently verify actual extracted monetary source records."""

    positive_id(company_id, "company")
    positive_id(payroll_period_id, "payroll period")

    if not all(
        isinstance(part, list)
        for part in (reported_lines, source_rows, calculation_lines)
    ):
        raise FinancialReconciliationError(
            "Persisted allocation collections required"
        )

    if not reported_lines:
        return blocked(
            company_id,
            payroll_period_id,
            "NO_PERSISTED_REPORT_LINES",
        )

    calculations = {}

    for record in calculation_lines:
        key = positive_id(record.get("id"), "calculation line")

        if key in calculations:
            raise FinancialReconciliationError(
                "Duplicate payroll calculation line"
            )

        if record.get("company_id") != company_id:
            raise FinancialReconciliationError(
                "Cross-company payroll calculation"
            )

        if record.get("payroll_period_id") != payroll_period_id:
            raise FinancialReconciliationError(
                "Cross-period payroll calculation"
            )

        calculations[key] = record

    grouped = {}

    for source in source_rows:
        source_id = positive_id(source.get("id"), "source ID")
        line_id = positive_id(
            source.get("statutory_line_id"),
            "statutory line ID",
        )
        calculation_line_id = positive_id(
            source.get("payroll_calculation_line_id"),
            "calculation line ID",
        )

        if source.get("company_id") != company_id:
            raise FinancialReconciliationError(
                "Cross-company statutory source"
            )

        if calculation_line_id not in calculations:
            raise FinancialReconciliationError(
                "Missing or out-of-period payroll source"
            )

        grouped.setdefault(line_id, [])

        if any(
            previous["id"] == source_id
            for previous in grouped[line_id]
        ):
            raise FinancialReconciliationError(
                "Duplicate statutory source ID"
            )

        grouped[line_id].append(source)

    results = []
    seen = set()
    verified = 0
    total_reported = Decimal("0.00")
    total_allocated = Decimal("0.00")

    for line in reported_lines:
        line_id = positive_id(line.get("id"), "report line")

        if line_id in seen:
            raise FinancialReconciliationError(
                "Duplicate statutory report line"
            )

        seen.add(line_id)

        if (
            line.get("company_id") != company_id
            or line.get("payroll_period_id") != payroll_period_id
        ):
            raise FinancialReconciliationError(
                "Regulatory report scope mismatch"
            )

        reported = money(line.get("reported_amount"))

        sources = grouped.get(line_id, [])
        allocated = sum(
            (money(row["source_amount"]) for row in sources),
            Decimal("0.00"),
        )

        total_reported += reported
        total_allocated += allocated

        if not sources:
            status = "MISSING_SOURCES"
        elif allocated != reported:
            status = "AMOUNT_MISMATCH"
        else:
            status = "RECONCILED"
            verified += 1

        results.append({
            "line_id": line_id,
            "status": status,
            "source_count": len(sources),
            "reported_amount": str(reported),
            "allocated_amount": str(allocated),
            "difference": str(reported - allocated),
        })

    if set(grouped) - seen:
        raise FinancialReconciliationError(
            "Orphan statutory source allocation"
        )

    return {
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "status": (
            "FINANCIAL_LINES_RECONCILED"
            if verified == len(reported_lines)
            else "NOT_VERIFIED"
        ),
        "verified_lines": verified,
        "line_count": len(reported_lines),
        "reported_total": str(total_reported),
        "allocated_total": str(total_allocated),
        "lines": results,
        "statutory_mapping_verified": False,
        "official_generation": "BLOCKED",
    }


async def reconcile_persisted_allocations(
    db,
    *,
    company_id,
    payroll_period_id,
):
    """Independently load and reconcile real PostgreSQL records."""

    positive_id(company_id, "company")
    positive_id(payroll_period_id, "payroll period")

    def discover(session):
        connection = session.connection()
        inspector = inspect(connection)
        existing = set(inspector.get_table_names())

        if any(name not in existing for name in TABLES):
            return {
                "missing_tables": sorted(set(TABLES) - existing),
            }

        metadata = MetaData()
        tables = {
            name: Table(name, metadata, autoload_with=connection)
            for name in TABLES
        }

        schema = {
            name: {
                "columns": set(tables[name].c.keys()),
                "foreign_keys": inspector.get_foreign_keys(name),
            }
            for name in TABLES
        }

        return {
            "tables": tables,
            "schema": schema,
        }

    discovery = await db.run_sync(discover)

    if "missing_tables" in discovery:
        return blocked(
            company_id,
            payroll_period_id,
            "MISSING_TABLES",
            missing_tables=discovery["missing_tables"],
        )

    t = discovery["tables"]
    schema = discovery["schema"]

    def has(table, *names):
        return set(names).issubset(schema[table]["columns"])

    # Do not invent report-to-period or payroll ownership links.
    required_fks = (
        (
            "payroll_statutory_result_lines",
            "payroll_statutory_results",
        ),
        (
            "payroll_statutory_line_sources",
            "payroll_statutory_result_lines",
        ),
        (
            "payroll_statutory_line_sources",
            "payroll_calculation_lines",
        ),
        ("payroll_calculation_lines", "payroll_calculations"),
    )

    joins = {}

    for child, parent in required_fks:
        fk = one_foreign_key(schema, child, parent)

        if fk is None:
            return blocked(
                company_id,
                payroll_period_id,
                "UNVERIFIED_FOREIGN_KEY",
                relationship=child + " -> " + parent,
            )

        joins[(child, parent)] = fk

    if not has(
        "payroll_statutory_results",
        "id", "company_id", "payroll_period_id",
    ):
        return blocked(
            company_id,
            payroll_period_id,
            "UNVERIFIED_REPORT_SCOPE",
        )

    if not has(
        "payroll_calculations",
        "id", "company_id", "payroll_period_id",
    ):
        return blocked(
            company_id,
            payroll_period_id,
            "UNVERIFIED_PAYROLL_SCOPE",
        )

    if not has(
        "payroll_statutory_line_sources",
        "id", "company_id", "source_amount",
    ):
        return blocked(
            company_id,
            payroll_period_id,
            "UNVERIFIED_SOURCE_AMOUNT_SCHEMA",
        )

    available_amounts = [
        name
        for name in AMOUNT_CANDIDATES
        if name in schema["payroll_statutory_result_lines"]["columns"]
    ]

    if len(available_amounts) != 1:
        return blocked(
            company_id,
            payroll_period_id,
            "AMBIGUOUS_REPORTED_AMOUNT",
            available_amount_fields=available_amounts,
        )

    amount_field = available_amounts[0]

    period_table = t["payroll_periods"]

    period = (
        await db.execute(
            select(period_table.c.id).where(
                period_table.c.id == payroll_period_id,
                period_table.c.company_id == company_id,
            )
        )
    ).scalar_one_or_none()

    if period is None:
        raise FinancialReconciliationError(
            "Payroll period does not belong to company"
        )

    report = t["payroll_statutory_results"]
    line = t["payroll_statutory_result_lines"]
    source = t["payroll_statutory_line_sources"]
    calculation_line = t["payroll_calculation_lines"]
    calculation = t["payroll_calculations"]

    report_join = joins[
        (
            "payroll_statutory_result_lines",
            "payroll_statutory_results",
        )
    ]

    source_line_join = joins[
        (
            "payroll_statutory_line_sources",
            "payroll_statutory_result_lines",
        )
    ]

    source_calc_join = joins[
        (
            "payroll_statutory_line_sources",
            "payroll_calculation_lines",
        )
    ]

    calc_parent_join = joins[
        (
            "payroll_calculation_lines",
            "payroll_calculations",
        )
    ]

    report_rows = (
        await db.execute(
            select(
                line.c.id.label("id"),
                line.c[amount_field].label("reported_amount"),
            )
            .select_from(
                line.join(
                    report,
                    line.c[report_join[0]]
                    == report.c[report_join[1]],
                )
            )
            .where(
                report.c.company_id == company_id,
                report.c.payroll_period_id == payroll_period_id,
            )
            .limit(MAX_ROWS + 1)
        )
    ).mappings().all()

    if len(report_rows) > MAX_ROWS:
        return blocked(
            company_id,
            payroll_period_id,
            "REPORT_ROW_LIMIT_EXCEEDED",
        )

    if not report_rows:
        return blocked(
            company_id,
            payroll_period_id,
            "NO_PERSISTED_REPORT_LINES",
        )

    statutory_ids = [item["id"] for item in report_rows]

    linked_rows = (
        await db.execute(
            select(
                source.c.id.label("source_id"),
                line.c.id.label("statutory_line_id"),
                source.c[source_calc_join[0]].label(
                    "calculation_line_id"
                ),
                source.c.company_id.label("source_company_id"),
                source.c.source_amount.label("source_amount"),
                calculation.c.company_id.label("calculation_company_id"),
                calculation.c.payroll_period_id.label(
                    "calculation_period_id"
                ),
            )
            .select_from(
                source.join(
                    line,
                    source.c[source_line_join[0]]
                    == line.c[source_line_join[1]],
                )
                .join(
                    calculation_line,
                    source.c[source_calc_join[0]]
                    == calculation_line.c[source_calc_join[1]],
                )
                .join(
                    calculation,
                    calculation_line.c[calc_parent_join[0]]
                    == calculation.c[calc_parent_join[1]],
                )
            )
            .where(line.c.id.in_(statutory_ids))
            .limit(MAX_ROWS + 1)
        )
    ).mappings().all()

    if len(linked_rows) > MAX_ROWS:
        return blocked(
            company_id,
            payroll_period_id,
            "SOURCE_ROW_LIMIT_EXCEEDED",
        )

    verified_sources = []
    verified_calculations = {}

    for row in linked_rows:
        if (
            row["source_company_id"] != company_id
            or row["calculation_company_id"] != company_id
            or row["calculation_period_id"] != payroll_period_id
        ):
            raise FinancialReconciliationError(
                "Persisted cross-company or cross-period source"
            )

        verified_sources.append({
            "id": row["source_id"],
            "statutory_line_id": row["statutory_line_id"],
            "payroll_calculation_line_id": row[
                "calculation_line_id"
            ],
            "source_amount": row["source_amount"],
            "company_id": company_id,
        })

        verified_calculations[row["calculation_line_id"]] = {
            "id": row["calculation_line_id"],
            "company_id": company_id,
            "payroll_period_id": payroll_period_id,
        }

    lines = [
        {
            "id": row["id"],
            "company_id": company_id,
            "payroll_period_id": payroll_period_id,
            "reported_amount": row["reported_amount"],
        }
        for row in report_rows
    ]

    result = verify_allocations(
        lines,
        verified_sources,
        list(verified_calculations.values()),
        company_id=company_id,
        payroll_period_id=payroll_period_id,
    )

    result["data_origin"] = "PERSISTED_POSTGRESQL"
    result["reported_amount_field"] = amount_field
    result["statutory_mapping_verified"] = False
    result["official_generation"] = "BLOCKED"

    return result
