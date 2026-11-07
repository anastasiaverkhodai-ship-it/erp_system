"""Validated employment-event sources for internal D5/D6 review.

No statutory event classifications or official report rows are
created here. Reversed and reversal records are excluded.
"""

from datetime import date

from sqlalchemy import exists, select

from app.schemas.employment_termination_basis import (
    EmploymentTerminationBasis,
)


class D5EventInventoryError(ValueError):
    pass


SUPPORTED_EVENTS = frozenset(
    {"hire", "transfer", "termination"}
)


def validate_d5_event_record(record):
    """Reject malformed HR source evidence before review hashing."""

    if not isinstance(record, dict):
        raise D5EventInventoryError(
            "Structured HR event required"
        )

    for key in (
        "id",
        "company_id",
        "employment_contract_id",
    ):
        value = record.get(key)

        if type(value) is not int or value <= 0:
            raise D5EventInventoryError(
                "Invalid HR event identity: " + key
            )

    kind = record.get("event_type")

    if kind not in SUPPORTED_EVENTS:
        raise D5EventInventoryError(
            "Unsupported HR event type"
        )

    if record.get("reversal_of_id") is not None:
        raise D5EventInventoryError(
            "Reversal cannot enter active HR inventory"
        )

    if type(record.get("effective_date")) is not date:
        raise D5EventInventoryError(
            "Documented effective date required"
        )

    if type(record.get("order_date")) is not date:
        raise D5EventInventoryError(
            "Documented order date required"
        )

    for key in ("order_number", "reason", "request_key"):
        value = record.get(key)

        if not isinstance(value, str) or not value.strip():
            raise D5EventInventoryError(
                "Documented HR source missing: " + key
            )

    for key in ("before_state", "after_state"):
        if not isinstance(record.get(key), dict):
            raise D5EventInventoryError(
                "Historical HR state missing: " + key
            )

    payload = record.get("request_payload")

    if not isinstance(payload, dict):
        raise D5EventInventoryError(
            "Documented HR request payload required"
        )

    basis = payload.get("termination_basis")

    if kind == "termination":
        if not isinstance(basis, dict):
            raise D5EventInventoryError(
                "Termination legal basis missing"
            )

        try:
            EmploymentTerminationBasis.model_validate(basis)
        except Exception as exc:
            raise D5EventInventoryError(
                "Invalid termination legal basis"
            ) from exc

    elif basis is not None:
        raise D5EventInventoryError(
            "Nontermination has termination basis"
        )

    return record


def active_employment_event_query(table, company_id, limit):
    """SQL-level anti-join excludes reversals of original HR events."""

    if type(company_id) is not int or company_id <= 0:
        raise D5EventInventoryError(
            "Valid company required"
        )

    if type(limit) is not int or limit <= 0:
        raise D5EventInventoryError(
            "Valid source limit required"
        )

    required = {
        "id",
        "company_id",
        "event_type",
        "reversal_of_id",
    }

    if not required.issubset(set(table.c.keys())):
        raise D5EventInventoryError(
            "Incomplete employment event schema"
        )

    reversal = table.alias("d5_inventory_reversal")

    was_reversed = exists(
        select(1).select_from(reversal).where(
            reversal.c.company_id == table.c.company_id,
            reversal.c.reversal_of_id == table.c.id,
            reversal.c.event_type == "reversal",
        )
    )

    return (
        select(table)
        .where(
            table.c.company_id == company_id,
            table.c.event_type.in_(
                ("hire", "transfer", "termination")
            ),
            table.c.reversal_of_id.is_(None),
            ~was_reversed,
        )
        .order_by(table.c.id)
        .limit(limit + 1)
    )


async def load_active_employment_event_records(
    db,
    *,
    table,
    company_id,
    limit,
):
    """Read all active HR events; date filtering follows in source control.

    Return limit + 1 to detect truncated source coverage.
    """

    statement = active_employment_event_query(
        table,
        company_id,
        limit,
    )

    rows = (
        await db.execute(statement)
    ).mappings().all()

    if len(rows) > limit:
        raise D5EventInventoryError(
            "Employment event source coverage exceeded"
        )

    return [
        validate_d5_event_record(dict(row))
        for row in rows
    ]
