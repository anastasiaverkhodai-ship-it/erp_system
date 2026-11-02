"""Read-only payroll revision chain review for regulatory evidence.

Distinguishes active calculation revisions from replaced historical
calculations. Does not declare statutory correction filing ready.

A positive chain result is NOT a positive financial correction
reconciliation, which requires version-scoped source evidence.
"""

from collections import defaultdict

from sqlalchemy import MetaData, Table, inspect, select

from app.services.payroll_regulatory_financial_reconciliation import (
    FinancialReconciliationError,
    positive_id,
)


def review_revision_chain(
    calculations,
    corrections,
    statutory_results,
    *,
    company_id,
    payroll_period_id,
):
    positive_id(company_id, "company")
    positive_id(payroll_period_id, "payroll period")

    if not all(
        isinstance(rows, list)
        for rows in (
            calculations,
            corrections,
            statutory_results,
        )
    ):
        raise FinancialReconciliationError(
            "Revision review requires record lists"
        )

    by_id = {}
    contract_groups = defaultdict(list)
    revision_pairs = set()

    for row in calculations:
        if not isinstance(row, dict):
            raise FinancialReconciliationError(
                "Invalid calculation record"
            )

        identifier = positive_id(
            row.get("id"),
            "calculation",
        )
        contract_id = positive_id(
            row.get("employment_contract_id"),
            "contract",
        )
        revision = positive_id(
            row.get("revision"),
            "revision",
        )

        if (
            row.get("company_id") != company_id
            or row.get("payroll_period_id")
            != payroll_period_id
        ):
            raise FinancialReconciliationError(
                "Cross-company or cross-period calculation"
            )

        if identifier in by_id:
            raise FinancialReconciliationError(
                "Duplicate payroll calculation ID"
            )

        pair = (contract_id, revision)
        if pair in revision_pairs:
            raise FinancialReconciliationError(
                "Duplicate contract revision"
            )

        revision_pairs.add(pair)
        by_id[identifier] = row
        contract_groups[contract_id].append(identifier)

    if not by_id:
        return {
            "status": "NOT_VERIFIED",
            "reason": "NO_PAYROLL_CALCULATIONS",
            "active_calculation_ids": [],
            "historical_calculation_ids": [],
            "official_generation": "BLOCKED",
        }

    outgoing = {}
    incoming = {}

    for link in corrections:
        if not isinstance(link, dict):
            raise FinancialReconciliationError(
                "Invalid correction record"
            )

        if link.get("company_id") != company_id:
            raise FinancialReconciliationError(
                "Cross-company correction"
            )

        original = positive_id(
            link.get("original_payroll_calculation_id"),
            "original calculation",
        )
        replacement = positive_id(
            link.get("replacement_payroll_calculation_id"),
            "replacement calculation",
        )

        if original == replacement:
            raise FinancialReconciliationError(
                "Self-replacement is forbidden"
            )

        if original not in by_id or replacement not in by_id:
            raise FinancialReconciliationError(
                "Correction references an out-of-period calculation"
            )

        a = by_id[original]
        b = by_id[replacement]

        if a["employment_contract_id"] != b["employment_contract_id"]:
            raise FinancialReconciliationError(
                "Replacement changes employment contract"
            )

        if b["revision"] <= a["revision"]:
            raise FinancialReconciliationError(
                "Replacement revision must increase"
            )

        if original in outgoing:
            raise FinancialReconciliationError(
                "Forked correction chain"
            )

        if replacement in incoming:
            raise FinancialReconciliationError(
                "Multiple correction predecessors"
            )

        outgoing[original] = replacement
        incoming[replacement] = original

    active = sorted(set(by_id) - set(outgoing))
    historical = sorted(outgoing)

    # Each employment contract must have exactly one active
    # version and all its other versions must form one chain.
    for contract_id, ids in contract_groups.items():
        active_ids = [i for i in ids if i in active]

        if len(active_ids) != 1:
            raise FinancialReconciliationError(
                "Ambiguous active contract revision"
            )

        current = active_ids[0]
        visited = {current}

        while current in incoming:
            current = incoming[current]

            if current in visited:
                raise FinancialReconciliationError(
                    "Correction chain cycle"
                )

            visited.add(current)

        if visited != set(ids):
            raise FinancialReconciliationError(
                "Disconnected or missing correction history"
            )

    statutory_counts = defaultdict(int)

    for row in statutory_results:
        if not isinstance(row, dict):
            raise FinancialReconciliationError(
                "Invalid statutory record"
            )

        if row.get("company_id") != company_id:
            raise FinancialReconciliationError(
                "Cross-company statutory evidence"
            )

        calculation_id = positive_id(
            row.get("payroll_calculation_id"),
            "statutory calculation",
        )

        if calculation_id not in by_id:
            raise FinancialReconciliationError(
                "Out-of-period statutory evidence"
            )

        statutory_counts[calculation_id] += 1

    missing_active = sorted(
        identifier for identifier in active
        if statutory_counts[identifier] == 0
    )

    duplicates = sorted(
        identifier for identifier, count
        in statutory_counts.items()
        if count > 1
    )

    # A correction chain can be structurally verified without
    # claiming its active financial allocations are verified.
    chain_verified = (
        not missing_active
        and not duplicates
        and len(active) == len(contract_groups)
    )

    has_revisions = bool(historical)

    return {
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "status": (
            "REVISION_CHAIN_VERIFIED"
            if chain_verified else "NOT_VERIFIED"
        ),
        "calculation_count": len(by_id),
        "contract_count": len(contract_groups),
        "correction_count": len(corrections),
        "active_calculation_ids": active,
        "historical_calculation_ids": historical,
        "missing_active_statutory_ids": missing_active,
        "duplicate_statutory_ids": duplicates,
        "correction_financial_reconciliation": (
            "NOT_VERIFIED" if has_revisions
            else "NOT_APPLICABLE"
        ),
        "statutory_mapping_verified": False,
        "official_generation": "BLOCKED",
    }


async def review_persisted_revision_chain(
    db,
    *,
    company_id,
    payroll_period_id,
):
    """Read actual calculations, corrections and statutory headers."""

    positive_id(company_id, "company")
    positive_id(payroll_period_id, "period")

    def discover(session):
        connection = session.connection()
        inspector = inspect(connection)

        names = (
            "payroll_periods",
            "payroll_calculations",
            "payroll_corrections",
            "payroll_statutory_results",
        )

        existing = set(inspector.get_table_names())
        missing = sorted(set(names) - existing)

        if missing:
            return {"missing": missing}

        metadata = MetaData()

        tables = {
            name: Table(
                name,
                metadata,
                autoload_with=connection,
            )
            for name in names
        }

        required = {
            "payroll_periods": (
                "id", "company_id",
            ),
            "payroll_calculations": (
                "id",
                "company_id",
                "payroll_period_id",
                "employment_contract_id",
                "revision",
            ),
            "payroll_corrections": (
                "company_id",
                "original_payroll_calculation_id",
                "replacement_payroll_calculation_id",
            ),
            "payroll_statutory_results": (
                "company_id",
                "payroll_calculation_id",
            ),
        }

        for name, columns in required.items():
            if not set(columns).issubset(tables[name].c.keys()):
                return {
                    "missing_columns": name,
                }

        return {"tables": tables}

    discovery = await db.run_sync(discover)

    if "tables" not in discovery:
        return {
            "status": "NOT_VERIFIED",
            "reason": discovery,
            "official_generation": "BLOCKED",
        }

    tables = discovery["tables"]
    period = tables["payroll_periods"]
    calculation = tables["payroll_calculations"]
    correction = tables["payroll_corrections"]
    statutory = tables["payroll_statutory_results"]

    existing_period = (
        await db.execute(
            select(period.c.id).where(
                period.c.company_id == company_id,
                period.c.id == payroll_period_id,
            )
        )
    ).scalar_one_or_none()

    if existing_period is None:
        raise FinancialReconciliationError(
            "Payroll period does not belong to company"
        )

    calculations = (
        await db.execute(
            select(
                calculation.c.id,
                calculation.c.company_id,
                calculation.c.payroll_period_id,
                calculation.c.employment_contract_id,
                calculation.c.revision,
            ).where(
                calculation.c.company_id == company_id,
                calculation.c.payroll_period_id == payroll_period_id,
            )
        )
    ).mappings().all()

    ids = [row["id"] for row in calculations]

    if not ids:
        return {
            "status": "NOT_VERIFIED",
            "reason": "NO_PAYROLL_CALCULATIONS",
            "official_generation": "BLOCKED",
        }

    # Query both ends independently, so an invalid cross-period
    # correction is not hidden by an INNER JOIN.
    corrections = (
        await db.execute(
            select(
                correction.c.company_id,
                correction.c.original_payroll_calculation_id,
                correction.c.replacement_payroll_calculation_id,
            ).where(
                correction.c.company_id == company_id,
                (
                    correction.c.original_payroll_calculation_id.in_(ids)
                    | correction.c.replacement_payroll_calculation_id.in_(ids)
                ),
            )
        )
    ).mappings().all()

    statutory_results = (
        await db.execute(
            select(
                statutory.c.company_id,
                statutory.c.payroll_calculation_id,
            ).where(
                statutory.c.company_id == company_id,
                statutory.c.payroll_calculation_id.in_(ids),
            )
        )
    ).mappings().all()

    result = review_revision_chain(
        [dict(row) for row in calculations],
        [dict(row) for row in corrections],
        [dict(row) for row in statutory_results],
        company_id=company_id,
        payroll_period_id=payroll_period_id,
    )

    result["data_origin"] = "PERSISTED_POSTGRESQL"
    return result
