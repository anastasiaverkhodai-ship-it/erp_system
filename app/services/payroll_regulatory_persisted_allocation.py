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
    """Reconcile persisted evidence through verified composite FK paths.

    Statutory header -> payroll calculation -> payroll period.
    Statutory source -> statutory line and payroll calculation line.

    Never assume a statutory header has payroll_period_id.
    Never treat absence of persisted evidence as successful.
    """

    positive_id(company_id, "company")
    positive_id(payroll_period_id, "payroll period")

    def discover(session):
        connection = session.connection()
        inspector = inspect(connection)
        available = set(inspector.get_table_names())

        missing = sorted(set(TABLES) - available)

        if missing:
            return {"missing_tables": missing}

        metadata = MetaData()

        tables = {
            name: Table(name, metadata, autoload_with=connection)
            for name in TABLES
        }

        foreign_keys = {
            name: inspector.get_foreign_keys(name)
            for name in TABLES
        }

        return {
            "tables": tables,
            "foreign_keys": foreign_keys,
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
    fks = discovery["foreign_keys"]

    # The required FK edges are precisely those confirmed by
    # ORM, migrations and the real PostgreSQL schema probe.
    edges = (
        (
            "payroll_statutory_result_lines",
            "payroll_statutory_results",
            ("company_id", "payroll_statutory_result_id"),
            ("company_id", "id"),
        ),
        (
            "payroll_statutory_results",
            "payroll_calculations",
            ("company_id", "payroll_calculation_id"),
            ("company_id", "id"),
        ),
        (
            "payroll_statutory_line_sources",
            "payroll_statutory_result_lines",
            ("company_id", "statutory_line_id"),
            ("company_id", "id"),
        ),
        (
            "payroll_statutory_line_sources",
            "payroll_calculation_lines",
            ("company_id", "payroll_calculation_line_id"),
            ("company_id", "id"),
        ),
        (
            "payroll_calculation_lines",
            "payroll_calculations",
            ("company_id", "payroll_calculation_id"),
            ("company_id", "id"),
        ),
        (
            "payroll_calculations",
            "payroll_periods",
            ("company_id", "payroll_period_id"),
            ("company_id", "id"),
        ),
    )

    for child, parent, local, remote in edges:
        matches = [
            fk
            for fk in fks[child]
            if fk.get("referred_table") == parent
            and tuple(fk.get("constrained_columns") or ()) == local
            and tuple(fk.get("referred_columns") or ()) == remote
        ]

        if len(matches) != 1:
            return blocked(
                company_id,
                payroll_period_id,
                "UNVERIFIED_COMPOSITE_FOREIGN_KEY",
                relationship=child + " -> " + parent,
            )

    required_columns = {
        "payroll_periods": (
            "id", "company_id",
        ),
        "payroll_calculations": (
            "id", "company_id", "payroll_period_id",
        ),
        "payroll_calculation_lines": (
            "id", "company_id", "payroll_calculation_id",
            "amount", "currency_code",
        ),
        "payroll_statutory_results": (
            "id", "company_id", "payroll_calculation_id",
            "currency_code",
        ),
        "payroll_statutory_result_lines": (
            "id", "company_id", "payroll_statutory_result_id",
            "amount", "base_amount", "component", "currency_code",
        ),
        "payroll_statutory_line_sources": (
            "id", "company_id", "statutory_line_id",
            "payroll_calculation_line_id", "source_amount",
            "income_effective_from", "income_effective_to",
        ),
    }

    for name, columns in required_columns.items():
        if not set(columns).issubset(t[name].c.keys()):
            return blocked(
                company_id,
                payroll_period_id,
                "MISSING_REQUIRED_COLUMNS",
                table=name,
                missing_columns=sorted(
                    set(columns) - set(t[name].c.keys())
                ),
            )

    period = t["payroll_periods"]
    calculation = t["payroll_calculations"]
    calculation_line = t["payroll_calculation_lines"]
    header = t["payroll_statutory_results"]
    line = t["payroll_statutory_result_lines"]
    source = t["payroll_statutory_line_sources"]

    period_exists = (
        await db.execute(
            select(period.c.id).where(
                period.c.company_id == company_id,
                period.c.id == payroll_period_id,
            )
        )
    ).scalar_one_or_none()

    if period_exists is None:
        raise FinancialReconciliationError(
            "Payroll period does not belong to company"
        )

    # Real header ownership:
    # statutory result -> payroll calculation -> period.
    report_join = (
        header.join(
            calculation,
            (header.c.company_id == calculation.c.company_id)
            & (
                header.c.payroll_calculation_id
                == calculation.c.id
            ),
        )
        .join(
            line,
            (line.c.company_id == header.c.company_id)
            & (
                line.c.payroll_statutory_result_id
                == header.c.id
            ),
        )
    )

    report_rows = (
        await db.execute(
            select(
                line.c.id.label("line_id"),
                line.c.base_amount.label("reported_amount"),
                line.c.amount.label("tax_amount"),
                line.c.component.label("component"),
                line.c.currency_code.label("currency"),
                calculation.c.id.label("calculation_id"),
                header.c.currency_code.label("header_currency"),
            )
            .select_from(report_join)
            .where(
                calculation.c.company_id == company_id,
                calculation.c.payroll_period_id
                == payroll_period_id,
            )
            .order_by(line.c.id)
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

    line_ids = [row["line_id"] for row in report_rows]

    # Source records are read separately to detect orphaned /
    # mismatched bindings instead of hiding them with INNER JOIN.
    source_rows = (
        await db.execute(
            select(
                source.c.id.label("source_id"),
                source.c.statutory_line_id.label("statutory_line_id"),
                source.c.payroll_calculation_line_id.label(
                    "calculation_line_id"
                ),
                source.c.company_id.label("source_company"),
                source.c.source_amount.label("source_amount"),
                source.c.income_effective_from.label("income_from"),
                source.c.income_effective_to.label("income_to"),
            )
            .where(
                source.c.company_id == company_id,
                source.c.statutory_line_id.in_(line_ids),
            )
            .order_by(source.c.id)
            .limit(MAX_ROWS + 1)
        )
    ).mappings().all()

    if len(source_rows) > MAX_ROWS:
        return blocked(
            company_id,
            payroll_period_id,
            "SOURCE_ROW_LIMIT_EXCEEDED",
        )

    calculation_line_ids = {
        row["calculation_line_id"] for row in source_rows
    }

    actual_calculation_lines = []

    if calculation_line_ids:
        actual_calculation_lines = (
            await db.execute(
                select(
                    calculation_line.c.id.label("id"),
                    calculation_line.c.company_id.label("company_id"),
                    calculation_line.c.payroll_calculation_id.label(
                        "calculation_id"
                    ),
                    calculation_line.c.amount.label("payroll_amount"),
                    calculation_line.c.currency_code.label("currency"),
                    calculation.c.payroll_period_id.label("period_id"),
                )
                .select_from(
                    calculation_line.join(
                        calculation,
                        (
                            calculation_line.c.company_id
                            == calculation.c.company_id
                        )
                        & (
                            calculation_line.c.payroll_calculation_id
                            == calculation.c.id
                        ),
                    )
                )
                .where(
                    calculation_line.c.id.in_(
                        calculation_line_ids
                    ),
                    calculation_line.c.company_id == company_id,
                )
            )
        ).mappings().all()

    by_calculation_line = {
        row["id"]: row for row in actual_calculation_lines
    }

    by_statutory_line = {
        row["line_id"]: row for row in report_rows
    }

    groups = {}

    for row in source_rows:
        statutory = by_statutory_line.get(
            row["statutory_line_id"]
        )
        payroll = by_calculation_line.get(
            row["calculation_line_id"]
        )

        if statutory is None or payroll is None:
            raise FinancialReconciliationError(
                "Unresolved persisted source binding"
            )

        if (
            row["source_company"] != company_id
            or payroll["company_id"] != company_id
            or payroll["period_id"] != payroll_period_id
        ):
            raise FinancialReconciliationError(
                "Cross-company or cross-period source"
            )

        if payroll["calculation_id"] != statutory["calculation_id"]:
            raise FinancialReconciliationError(
                "Source linked to a different payroll calculation"
            )

        if (
            payroll["currency"] != statutory["currency"]
            or statutory["currency"]
            != statutory["header_currency"]
        ):
            raise FinancialReconciliationError(
                "Persisted financial currency mismatch"
            )

        if (
            row["income_from"] is None
            or row["income_to"] is None
            or row["income_from"] > row["income_to"]
        ):
            raise FinancialReconciliationError(
                "Invalid dated financial allocation"
            )

        groups.setdefault(
            row["statutory_line_id"], []
        ).append(row)

    from decimal import Decimal

    totals_reported = Decimal("0.00")
    totals_allocated = Decimal("0.00")
    output = []
    verified = 0

    for row in report_rows:
        reported = money(row["reported_amount"])
        statutory_tax = money(row["tax_amount"])
        allocations = groups.get(row["line_id"], [])

        allocated = sum(
            (money(item["source_amount"]) for item in allocations),
            Decimal("0.00"),
        )

        totals_reported += reported
        totals_allocated += allocated

        if not allocations:
            status = "MISSING_SOURCES"
        elif allocated != reported:
            status = "AMOUNT_MISMATCH"
        else:
            status = "RECONCILED"
            verified += 1

        output.append({
            "line_id": row["line_id"],
            "status": status,
            "source_count": len(allocations),
            "reported_amount": str(reported),
            "statutory_tax_amount": str(statutory_tax),
            "allocated_amount": str(allocated),
            "difference": str(reported - allocated),
        })

    # This is an internal statutory-component reconciliation.
    # It is NOT approval of D1/4DF form-field mappings.
    return {
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "status": (
            "FINANCIAL_LINES_RECONCILED"
            if verified == len(report_rows)
            else "NOT_VERIFIED"
        ),
        "verified_lines": verified,
        "line_count": len(report_rows),
        "reported_total": str(totals_reported),
        "allocated_total": str(totals_allocated),
        "lines": output,
        "data_origin": "PERSISTED_POSTGRESQL",
        "reported_amount_field": "base_amount",
        "statutory_mapping_verified": False,
        "official_generation": "BLOCKED",
    }
