from app.services.payroll_d5_event_inventory import (
    load_active_employment_event_records,
)

"""Internal D5/D6 source evidence and applicability controls.

No official normative event classification or filing output is
authorized here. All source queries are tenant-scoped and read-only.
"""

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import MetaData, Table, inspect, select

from app.models.payroll import PayrollPeriod


class D5D6SourceError(ValueError):
    pass


SOURCE_TABLES = (
    "hr_changes",
    "employment_contracts",
    "payroll_d5_professional_evidence",
    "employment_events",
)

EVENT_DATE_COLUMNS = (
    "event_date",
    "effective_date",
    "change_date",
    "effective_from",
    "start_date",
    "termination_date",
)

IDENTIFIER_COLUMNS = (
    "id",
    "hr_change_id",
    "employment_contract_id",
)

MAX_ROWS_PER_TABLE = 10000


def require_positive(value, field):
    if type(value) is not int or value <= 0:
        raise D5D6SourceError("Invalid " + field)
    return value


def to_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def canonical_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {
            str(k): canonical_value(v)
            for k, v in sorted(value.items(), key=lambda x: str(x[0]))
        }
    if isinstance(value, (tuple, list)):
        return [canonical_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def evidence_digest(record):
    if not isinstance(record, dict) or not record:
        raise D5D6SourceError("Invalid evidence record")

    payload = json.dumps(
        canonical_value(record),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_event_date(record):
    """Return an unambiguous event date, never a guessed date."""
    candidates = []

    for column in EVENT_DATE_COLUMNS:
        value = record.get(column)

        if value is not None:
            parsed = to_date(value)
            if parsed is None:
                raise D5D6SourceError(
                    "Invalid event date in " + column
                )
            candidates.append((column, parsed))

    if not candidates:
        return None, None

    # More than one date can mean a period rather than one event.
    # Do not silently classify a contract as a dated HR event.
    if len({value for _, value in candidates}) != 1:
        return None, None

    return candidates[0]


def prepare_source_evidence(
    *,
    table_name,
    records,
    company_id,
    period_start,
    period_end,
):
    """Validate scoped source rows; distinguish facts from unknowns."""

    require_positive(company_id, "company identifier")

    if table_name not in SOURCE_TABLES:
        raise D5D6SourceError("Unapproved source table")

    if (
        not isinstance(period_start, date)
        or not isinstance(period_end, date)
        or period_start > period_end
    ):
        raise D5D6SourceError("Invalid payroll period")

    if not isinstance(records, list):
        raise D5D6SourceError("Invalid source collection")

    if len(records) > MAX_ROWS_PER_TABLE:
        raise D5D6SourceError("Source result limit exceeded")

    seen = set()
    dated = []
    undated = []
    outside_period = 0

    for record in records:
        if not isinstance(record, dict):
            raise D5D6SourceError("Invalid source record")

        if record.get("company_id") != company_id:
            raise D5D6SourceError("Cross-company HR source")

        identifier = next(
            (
                record[name]
                for name in IDENTIFIER_COLUMNS
                if type(record.get(name)) is int
                and record[name] > 0
            ),
            None,
        )

        if identifier is None:
            raise D5D6SourceError("HR source identifier missing")

        if identifier in seen:
            raise D5D6SourceError("Duplicate HR source identifier")

        seen.add(identifier)

        field, event_date = resolve_event_date(record)
        digest = evidence_digest(record)

        item = {
            "source_table": table_name,
            "source_id": identifier,
            "source_sha256": digest,
            "company_id": company_id,
            "date_field": field,
            "event_date": (
                event_date.isoformat() if event_date else None
            ),
            "statutory_classification": "NOT_VERIFIED",
            "review_only": True,
        }

        if event_date is None:
            undated.append(item)
        elif period_start <= event_date <= period_end:
            dated.append(item)
        else:
            outside_period += 1

    dated.sort(
        key=lambda item: (
            item["event_date"],
            item["source_table"],
            item["source_id"],
        )
    )
    undated.sort(key=lambda item: item["source_id"])

    return {
        "source_table": table_name,
        "scanned_count": len(records),
        "dated_in_period_count": len(dated),
        "undated_or_ambiguous_count": len(undated),
        "dated_outside_period_count": outside_period,
        "dated_evidence": dated,
        "undated_evidence": undated,
    }


async def reflected_source_tables(db):
    def load(session):
        connection = session.connection()
        existing = set(
            inspect(connection).get_table_names()
        )

        result = {}

        for name in SOURCE_TABLES:
            if name not in existing:
                continue

            table = Table(
                name,
                MetaData(),
                autoload_with=connection,
            )

            if "company_id" not in table.c:
                raise D5D6SourceError(
                    "HR source has no tenant column: " + name
                )

            if "id" not in table.c:
                raise D5D6SourceError(
                    "HR source has no primary identifier: " + name
                )

            result[name] = table

        return result

    return await db.run_sync(load)


async def build_d5_d6_source_control(
    db,
    *,
    company_id,
    payroll_period_id,
):
    """Read PostgreSQL HR evidence and report unresolved D5/D6 gates."""

    require_positive(company_id, "company identifier")
    require_positive(payroll_period_id, "payroll period")

    period = await db.scalar(
        select(PayrollPeriod).where(
            PayrollPeriod.company_id == company_id,
            PayrollPeriod.id == payroll_period_id,
        )
    )

    if period is None:
        raise D5D6SourceError(
            "Payroll period not found in company"
        )

    tables = await reflected_source_tables(db)

    evidence = []
    missing_tables = []

    for name in SOURCE_TABLES:
        table = tables.get(name)

        if table is None:
            missing_tables.append(name)
            continue

        if name == "employment_events":
            records = await load_active_employment_event_records(
                db,
                table=table,
                company_id=company_id,
                limit=MAX_ROWS_PER_TABLE,
            )
        else:
            statement = (
                select(table)
                .where(table.c.company_id == company_id)
                .order_by(table.c.id)
                .limit(MAX_ROWS_PER_TABLE + 1)
            )

            records = [
                dict(row)
                for row in (await db.execute(statement)).mappings().all()
            ]

        if len(records) > MAX_ROWS_PER_TABLE:
            raise D5D6SourceError(
                "Source row limit exceeded: " + name
            )

        evidence.append(
            prepare_source_evidence(
                table_name=name,
                records=records,
                company_id=company_id,
                period_start=period.start_date,
                period_end=period.end_date,
            )
        )

    total_dated = sum(
        item["dated_in_period_count"]
        for item in evidence
    )
    total_ambiguous = sum(
        item["undated_or_ambiguous_count"]
        for item in evidence
    )

    return {
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "period_start": period.start_date.isoformat(),
        "period_end": period.end_date.isoformat(),
        "source_inventory": evidence,
        "missing_source_tables": missing_tables,
        "dated_evidence_count": total_dated,
        "ambiguous_evidence_count": total_ambiguous,
        "d5": {
            "source_inventory": "COLLECTED",
            "statutory_event_mapping": "NOT_APPROVED",
            "hr_event_coverage": "NOT_VERIFIED",
            "official_rows_generated": 0,
        },
        "d6": {
            "applicability": "NOT_DETERMINED",
            "source_coverage": "NOT_VERIFIED",
            "official_rows_generated": 0,
        },
        "review_only": True,
        "overall_status": "REQUIRES_NORMATIVE_REVIEW",
        "official_statutory_generation": "BLOCKED",
    }
